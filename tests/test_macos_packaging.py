"""Exercise staged Mac updates without touching Applications or real launch services."""
import os
import json
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-portable-export-macos.sh'


@unittest.skipUnless(sys.platform == 'darwin', 'Mac installer uses macOS bundle tools')
class StagedMacInstaller(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='PiRouter package & QA ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.support = self.root / 'Support & settings'
        self.support.mkdir()
        self.app = self.root / 'Applications & apps/PiRouter Exporter.app'
        self.agent_dir = self.root / 'Launch Agents & jobs'
        self.agent_dir.mkdir()
        self.config = self.support / 'portable-export.env'
        self.agent = self.agent_dir / 'com.pirouter.portable-export.plist'
        self.prebuilt = self.root / 'Fresh & staged.app'
        (self.prebuilt / 'Contents/MacOS').mkdir(parents=True)
        (self.prebuilt / 'Contents/MacOS/PiRouterExporter').write_text('new executable')
        (self.prebuilt / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.pirouter.portable-export'}))
        runtime = self.prebuilt / 'Contents/Frameworks/Python.framework/Versions/Current/bin'
        runtime.mkdir(parents=True)
        self.write_executable(runtime / 'python3', '''#!/bin/bash
unset PYTHONHOME
if [[ "$#" == 2 && "$1" == - ]]; then
  if [[ -n "${TEST_CHECKPOINT_CONFIG:-}" ]]; then printf '# saved during stop\\n' > "$TEST_CHECKPOINT_CONFIG"; fi
  if [[ "${TEST_CHECKPOINT_FAIL:-}" == 1 ]]; then exit 73; fi
fi
exec "$TEST_PYTHON" "$@"
''')
        self.bin = self.root / 'mock tools'
        self.bin.mkdir()
        self.write_executable(self.bin / 'codesign', '''#!/bin/bash
if [[ -n "${TEST_NEW_CONFIG:-}" && "$*" == *'.pirouter-install.'* ]]; then printf '# created during build\\n' > "$TEST_NEW_CONFIG"; fi
[[ "${TEST_CODESIGN_FAIL:-}" != 1 ]]
''')
        self.write_executable(self.bin / 'launchctl', '''#!/bin/bash
printf '%s\\n' "$*" >> "$TEST_SERVICE_LOG"
if [[ "$1" == print ]]; then [[ -f "$TEST_SERVICE_LOADED" ]]; exit; fi
if [[ "$1" == bootout && "${TEST_BOOTOUT_FAIL:-}" != 1 ]]; then rm -f "$TEST_SERVICE_LOADED"; fi
if [[ "$1" == bootstrap && "${TEST_BOOTSTRAP_FAIL:-}" == 1 && ! -f "$TEST_BOOTSTRAP_MARKER" ]]; then touch "$TEST_BOOTSTRAP_MARKER"; exit 71; fi
if [[ "$1" == bootstrap ]]; then touch "$TEST_SERVICE_LOADED"; fi
exit 0
''')
        self.write_executable(self.bin / 'open', '#!/bin/bash\nexit 79\n')
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(('PIROUTER_', 'EXPORT_', 'AUTOMATIC_', 'RSYNC_'))
                            and key != 'CONTINUOUS_EXPORT'}
        self.environment.update({
            'PATH': str(self.bin) + ':' + os.environ['PATH'],
            'PIROUTER_SUPPORT_DIR': str(self.support),
            'PIROUTER_APP_DIR': str(self.app),
            'PIROUTER_LAUNCH_AGENT_DIR': str(self.agent_dir),
            'PIROUTER_PREBUILT_APP': str(self.prebuilt),
            'PIROUTER_SKIP_SERVICE': '1', 'PIROUTER_NO_OPEN': '1',
            'TEST_PYTHON': sys.executable,
            'TEST_SERVICE_LOG': str(self.root / 'service.log'),
            'TEST_BOOTSTRAP_MARKER': str(self.root / 'bootstrap.failed'),
            'TEST_SERVICE_LOADED': str(self.root / 'service.loaded'),
        })

    @staticmethod
    def write_executable(path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def run_installer(self, **overrides):
        return subprocess.run(['/bin/bash', str(INSTALLER)],
                              env={**self.environment, **overrides}, text=True,
                              capture_output=True, timeout=30)

    def old_install(self, enabled=False):
        self.app.mkdir(parents=True)
        (self.app / 'old-version').write_text('keep old bundle')
        self.agent.write_text('keep old agent')
        (self.root / 'service.loaded').touch()
        key = self.root / 'SSH & fixture key'
        key.write_text('test fixture, not a real key')
        values = {
            'PIROUTER_URL': 'http://router.example', 'PIROUTER_TOKEN': 'fixture-token',
            'PIROUTER_LOCAL_URLS': 'http://one.example,http://two.example',
            'EXPORT_ENABLED': str(enabled).lower(), 'EXPORT_HOST': 'backup@fixture',
            'EXPORT_ROOT': '/mnt/user/custom-share', 'EXPORT_SSH_KEY': str(key),
            'EXPORT_SSH_PROXY_COMMAND': '/usr/bin/ssh -i "key & space" relay fixed-only',
            'CONTINUOUS_EXPORT': 'false', 'AUTOMATIC_EXPORT': 'false',
            'AUTOMATIC_EXPORT_MIN_HOURS': '72', 'RSYNC_BANDWIDTH_LIMIT_KB': '12345',
            'FUTURE_CUSTOM_SETTING': 'preserve & unknown value',
        }
        self.config.write_text('# custom comment\n' + ''.join(
            name + '=' + shlex.quote(value) + '\n' for name, value in values.items()))
        return self.config.read_bytes(), self.agent.read_bytes()

    def read_settings(self):
        result = subprocess.run(['/bin/bash', '-c',
            'source "$1"; env', 'fixture', str(self.config)],
            env={**self.environment, 'BASH_ENV': '/dev/null', 'SHELLOPTS': 'allexport'},
            text=True, capture_output=True, check=True)
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def test_upgrade_preserves_unknown_settings_and_export_policies(self):
        self.old_install()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_settings()
        self.assertEqual(values['FUTURE_CUSTOM_SETTING'], 'preserve & unknown value')
        self.assertEqual(values['AUTOMATIC_EXPORT'], 'false')
        self.assertEqual(values['AUTOMATIC_EXPORT_MIN_HOURS'], '72')
        self.assertEqual(values['RSYNC_BANDWIDTH_LIMIT_KB'], '12345')
        self.assertEqual(values['CONTINUOUS_EXPORT'], 'false')
        self.assertEqual(values['EXPORT_ROOT'], '/mnt/user/custom-share')
        self.assertEqual(values['PIROUTER_LOCAL_URLS'], 'http://one.example,http://two.example')
        self.assertIn('# custom comment', self.config.read_text())
        agent = plistlib.loads(self.agent.read_bytes())
        self.assertEqual(agent['ProgramArguments'][0], str(self.app / 'Contents/MacOS/PiRouterExporter'))
        self.assertEqual(agent['EnvironmentVariables']['PIROUTER_SUPPORT_DIR'], str(self.support))
        self.assertEqual(agent['Umask'], 0o077)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.app / 'Contents/MacOS/PiRouterExporter').exists())
        self.assertEqual(self.run_installer().returncode, 0)
        self.assertEqual(self.config.read_text().count('# BEGIN PIROUTER INSTALLER SETTINGS'), 1)

    def test_fresh_install_needs_no_credentials_and_disables_exports(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_settings()['EXPORT_ENABLED'], 'false')
        self.assertEqual(self.read_settings()['EXPORT_ROOT'], '/mnt/user/time-machine-austin')

    def test_invalid_signature_keeps_current_app_config_and_agent(self):
        config, agent = self.old_install()
        result = self.run_installer(TEST_CODESIGN_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.agent.read_bytes(), agent)
        self.assertTrue((self.app / 'old-version').exists())

    def test_new_first_run_settings_during_build_are_not_overwritten(self):
        result = self.run_installer(TEST_NEW_CONFIG=str(self.config))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '# created during build\n')
        self.assertFalse(self.app.exists())
        self.assertFalse(self.agent.exists())

    def test_checkpoint_failure_restores_already_stopped_background_job(self):
        config, agent = self.old_install(enabled=True)
        result = self.run_installer(PIROUTER_SKIP_SERVICE='false', TEST_CHECKPOINT_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.agent.read_bytes(), agent)
        self.assertTrue((self.app / 'old-version').exists())
        log = (self.root / 'service.log').read_text()
        self.assertIn('bootout ', log)
        self.assertIn('bootstrap ', log)

    def test_background_job_that_cannot_be_unloaded_blocks_replacement(self):
        config, agent = self.old_install(enabled=True)
        result = self.run_installer(PIROUTER_SKIP_SERVICE='false', TEST_BOOTOUT_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('could not be stopped', result.stderr)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.agent.read_bytes(), agent)
        self.assertTrue((self.app / 'old-version').exists())

    def test_settings_saved_during_process_stop_abort_the_swap(self):
        self.old_install(enabled=True)
        result = self.run_installer(PIROUTER_SKIP_SERVICE='false', TEST_CHECKPOINT_CONFIG=str(self.config))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), '# saved during stop\n')
        self.assertTrue((self.app / 'old-version').exists())
        self.assertIn('bootstrap ', (self.root / 'service.log').read_text())

    def test_failed_service_bootstrap_rolls_back_app_config_and_agent(self):
        config, agent = self.old_install(enabled=True)
        result = self.run_installer(PIROUTER_SKIP_SERVICE='false', TEST_BOOTSTRAP_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.agent.read_bytes(), agent)
        self.assertTrue((self.app / 'old-version').exists())
        self.assertEqual((self.root / 'service.log').read_text().count('bootstrap '), 2)


@unittest.skipUnless(sys.platform == 'darwin', 'Mac build uses macOS bundle tools')
class MacDistributionAssembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='PiRouter build & QA ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source & resources'
        scripts = self.source / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('portable-export-macos.sh', 'verify-time-machine-volumes.py',
                     'validate-portable-snapshot.py', 'portable-export-lock.py',
                     'portable-export-mounts.py', 'resumable-portable-export.py',
                     'portable-export-receiver.php'):
            (scripts / name).write_text('resource fixture')
        (scripts / 'portable-export-launcher.swift').write_text('immutable launcher fixture // "--runtime-check"')
        (scripts / 'bundle-python-runtime-macos.py').write_text('''import os, pathlib, plistlib, sys
assert sys.argv[1:3]==['--validate','--output']
root=pathlib.Path(sys.argv[3])
metadata=plistlib.loads((root/'Versions/Current/Resources/Python.app/Contents/Info.plist').read_bytes())
assert metadata['LSRequiresNativeExecution'] is True
assert metadata['LSArchitecturePriority']==['arm64','x86_64']
pathlib.Path(os.environ['TEST_RUNTIME_AUDIT_LOG']).write_text(str(root))
if os.environ.get('TEST_RUNTIME_AUDIT_FAIL'): raise SystemExit('Runtime is not universal2: missing arm64')
''')
        (scripts / 'portable-export.env').write_text('token must never ship')
        (scripts / 'id_ed25519').write_text('key must never ship')
        (self.source / 'assets').mkdir()
        (self.source / 'assets/PiRouterExporter.icns').write_bytes(b'icon fixture')
        self.framework = self.root / 'framework & runtime/Python.framework'
        binary = self.framework / 'Versions/Current/bin/python3'
        binary.parent.mkdir(parents=True)
        StagedMacInstaller.write_executable(binary,
            '#!/bin/bash\nunset PYTHONHOME\nif [[ "$1" == - && "$#" == 2 ]]; then exit "${TEST_PYTHON_TRANSLATED_FAIL:-0}"; fi\nexec "$TEST_PYTHON" "$@"\n')
        self.runtime_info = self.framework / 'Versions/Current/Resources/Python.app/Contents/Info.plist'
        self.runtime_info.parent.mkdir(parents=True)
        self.runtime_info.write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.python.python',
            'LSRequiresNativeExecution': False,
            'LSArchitecturePriority': ['x86_64', 'arm64'],
            'KeepRuntimeMetadata': 'preserved',
        }))
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.write_tool('xcrun', r'''import json, os, pathlib, sys
args=sys.argv[1:]
if args==['--show-sdk-path']: print('/fixture/SDK'); raise SystemExit(0)
output=pathlib.Path(args[args.index('-o')+1]); source=pathlib.Path(args[-3])
output.write_text('#!/bin/bash\n# '+source.read_text()+'\nexit "${TEST_NATIVE_CHECK_FAIL:-0}"\n')
output.chmod(0o755)
with open(os.environ['TEST_BUILD_LOG'],'a') as log: log.write(json.dumps(args)+'\n')
if os.environ.get('TEST_MUTATE_SOURCE'):
 pathlib.Path(os.environ['TEST_MUTATE_SOURCE']).write_text('edited source after first slice')
''')
        self.write_tool('lipo', r'''import pathlib, sys
args=sys.argv[1:]
if '-create' in args:
 pathlib.Path(args[args.index('-output')+1]).write_text(pathlib.Path(args[1]).read_text()+'\n'+pathlib.Path(args[2]).read_text())
 pathlib.Path(args[args.index('-output')+1]).chmod(0o755)
''')
        self.write_tool('codesign', 'pass\n')
        self.write_tool('security', 'pass\n')
        self.write_tool('hdiutil', r'''import json, os, pathlib, sys
args=sys.argv[1:]
if args[0]=='create':
 if os.environ.get('TEST_DMG_FAIL'): raise SystemExit(61)
 root=pathlib.Path(args[args.index('-srcfolder')+1])
 pathlib.Path(os.environ['TEST_DMG_CONTENTS']).write_text(json.dumps({p.name:os.readlink(p) if p.is_symlink() else 'file' for p in root.iterdir()}))
 pathlib.Path(args[-1]).write_bytes(b'dmg fixture')
''')
        self.output = self.root / 'distribution & output'
        self.environment = {
            **os.environ, 'PATH': str(self.tools)+':'+os.environ['PATH'],
            'PIROUTER_SOURCE_DIR': str(self.source), 'PIROUTER_BUILD_DIR': str(self.output),
            'PIROUTER_PYTHON_FRAMEWORK': str(self.framework),
            'PIROUTER_CODE_SIGN_IDENTITY': '-', 'TEST_PYTHON': sys.executable,
            'TEST_BUILD_LOG': str(self.root / 'build.log'),
            'TEST_DMG_CONTENTS': str(self.root / 'dmg.json'),
            'TEST_RUNTIME_AUDIT_LOG': str(self.root / 'runtime-audit.log'),
        }
        self.environment.pop('PIROUTER_APP_OUTPUT', None)
        self.environment.pop('PIROUTER_DMG_OUTPUT', None)
        self.environment.pop('PIROUTER_BUILD_DMG', None)

    def write_tool(self, name, code):
        StagedMacInstaller.write_executable(self.tools / name, '#!'+sys.executable+'\n'+code)

    def build(self, **overrides):
        return subprocess.run(['/bin/bash', str(ROOT / 'scripts/build-macos-app.sh')],
                              env={**self.environment, **overrides}, capture_output=True,
                              text=True, timeout=30)

    def test_universal_build_uses_identical_source_and_only_public_resources(self):
        result = self.build(TEST_MUTATE_SOURCE=str(self.source/'scripts/portable-export-launcher.swift'))
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.output / 'PiRouter Exporter.app'
        info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
        self.assertEqual(info['LSMinimumSystemVersion'], '13.0')
        self.assertEqual(info['CFBundleIdentifier'], 'local.pirouter.portable-export')
        self.assertTrue(info['LSRequiresNativeExecution'])
        self.assertEqual(info['LSArchitecturePriority'], ['arm64', 'x86_64'])
        self.assertEqual(info['CFBundleShortVersionString'], '3.0.1')
        self.assertEqual(info['CFBundleVersion'], '4')
        nested = plistlib.loads((app/'Contents/Frameworks/Python.framework/Versions/Current/Resources/Python.app/Contents/Info.plist').read_bytes())
        self.assertTrue(nested['LSRequiresNativeExecution'])
        self.assertEqual(nested['LSArchitecturePriority'], ['arm64', 'x86_64'])
        self.assertEqual(nested['KeepRuntimeMetadata'], 'preserved')
        self.assertFalse(plistlib.loads(self.runtime_info.read_bytes())['LSRequiresNativeExecution'])
        audit_path = (self.root/'runtime-audit.log').read_text()
        self.assertIn('.build.', audit_path)
        self.assertIn('Contents/Frameworks/Python.framework', audit_path)
        launcher = (app/'Contents/MacOS/PiRouterExporter').read_text()
        self.assertEqual(launcher.count('immutable launcher fixture'), 2)
        self.assertNotIn('edited source', launcher)
        calls = [json.loads(line) for line in (self.root/'build.log').read_text().splitlines()]
        self.assertEqual([args[args.index('-target')+1] for args in calls],
                         ['arm64-apple-macosx13.0', 'x86_64-apple-macosx13.0'])
        resources = {p.name for p in (app/'Contents/Resources').iterdir()}
        self.assertNotIn('portable-export.env', resources)
        self.assertNotIn('id_ed25519', resources)
        self.assertIn('INSTALLATION.txt', resources)
        self.assertEqual(json.loads((self.root/'dmg.json').read_text()), {
            'Applications':'/Applications', 'PiRouter Exporter.app':'file', 'Install Readme.txt':'file'})
        manifest = json.loads((self.output/'distribution-manifest.json').read_text())
        self.assertEqual(manifest['architectures'], ['arm64', 'x86_64'])
        self.assertEqual(manifest['signing'], 'ad-hoc')
        self.assertFalse(manifest['notarized'])
        self.assertTrue(manifest['requiresNativeExecution'])

    def test_runtime_audit_rejects_an_overridden_framework_missing_arm64(self):
        result = self.build(TEST_RUNTIME_AUDIT_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing arm64', result.stderr)
        self.assertFalse((self.output/'PiRouter Exporter.app').exists())

    def test_translated_python_or_launcher_fails_before_publication(self):
        for variable in ['TEST_PYTHON_TRANSLATED_FAIL', 'TEST_NATIVE_CHECK_FAIL']:
            with self.subTest(variable=variable):
                result = self.build(**{variable: '67'})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.output/'PiRouter Exporter.app').exists())

    def test_failed_image_creation_preserves_previous_distributables(self):
        self.output.mkdir()
        app = self.output/'PiRouter Exporter.app'
        app.mkdir()
        (app/'old-release').write_text('old app')
        image = self.output/'PiRouter-Exporter-3.0.1.dmg'
        image.write_bytes(b'old disk image')
        result = self.build(TEST_DMG_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app/'old-release').read_text(), 'old app')
        self.assertEqual(image.read_bytes(), b'old disk image')


if __name__ == '__main__':
    unittest.main()
