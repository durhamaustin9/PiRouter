"""Exercise Mac runtime selection without credentials, mounts, or network I/O."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / 'scripts/portable-export-macos.sh'


class MacOSRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pirouter-runtime-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.support = self.root / 'Application Support & Settings'
        self.support.mkdir()
        self.config = self.support / 'portable-export.env'
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(('PIROUTER_', 'EXPORT_', 'PYTHON'))
            and key not in ('SSL_CERT_FILE', 'CONTINUOUS_EXPORT', 'AUTOMATIC_EXPORT')
        }
        self.environment.update(HOME=str(self.root / 'home'),
                                PIROUTER_SUPPORT_DIR=str(self.support))

    def run_wrapper(self, **overrides):
        return subprocess.run(['/bin/bash', str(WRAPPER)],
                              env={**self.environment, **overrides},
                              capture_output=True, text=True, timeout=5)

    def test_dashboard_only_needs_no_export_credentials_or_python(self):
        contents = 'EXPORT_ENABLED=false\n'
        self.config.write_text(contents)
        result = self.run_wrapper(PIROUTER_PYTHON='/does/not/exist/python3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')
        self.assertEqual(self.config.read_text(), contents)
        self.assertEqual(list(self.support.iterdir()), [self.config])

    def test_no_background_mode_exits_before_export_credentials(self):
        self.config.write_text('EXPORT_ENABLED=true\n')
        result = self.run_wrapper(PIROUTER_NO_BACKGROUND='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.support / 'export.lock').exists())

    def test_invalid_export_toggle_fails_before_starting_work(self):
        self.config.write_text('EXPORT_ENABLED=perhaps\n')
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_ENABLED must be true or false', result.stderr)
        self.assertNotIn('PIROUTER_TOKEN is required', result.stderr)
        self.assertFalse((self.support / 'jobs').exists())

    def test_explicit_configuration_overrides_support_directory(self):
        self.config.write_text('EXPORT_ENABLED=invalid\n')
        selected = self.root / 'Other Settings.env'
        selected.write_text('EXPORT_ENABLED=false\n')
        result = self.run_wrapper(PIROUTER_PORTABLE_EXPORT_CONFIG=str(selected))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_text(), 'EXPORT_ENABLED=invalid\n')

    def runtime_prefix(self, bundled=True, executable=True, ca=True, **overrides):
        app = self.root / "A Mac's & Another App.app"
        resources = app / 'Contents/Resources'
        resources.mkdir(parents=True, exist_ok=True)
        framework = app / 'Contents/Frameworks/Python.framework/Versions'
        version = framework / '3.13'
        interpreter = version / 'bin/python3'
        if bundled:
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text('#!/bin/sh\nprintf bundled-runtime\n')
            interpreter.chmod(0o755 if executable else 0o644)
            (framework / 'Current').symlink_to('3.13', target_is_directory=True)
            if ca:
                certificate = version / 'etc/pirouter-ca.pem'
                certificate.parent.mkdir()
                certificate.write_text('fixture certificate bundle\n')
        self.config.write_text('\n'.join(f'{key}={shlex.quote(value)}' for key, value in {
            'PIROUTER_URL': 'http://router.example:3000',
            'PIROUTER_TOKEN': 'fixture-token',
            'EXPORT_HOST': 'backup@example.invalid',
            'EXPORT_ROOT': '/mnt/user/test',
            'EXPORT_SSH_KEY': '/test-only/key',
        }.items()) + '\n')
        prefix = WRAPPER.read_text().split('\njob_file=', 1)[0]
        # Stop before job files, lock acquisition, tmutil, or network actions.
        script = resources / 'portable-export-macos.sh'
        script.write_text(prefix + '''
printf '%s\\0' "${PIROUTER_PYTHON-}" "${PYTHONHOME-}" "${SSL_CERT_FILE-}" \
  "$PYTHONDONTWRITEBYTECODE" "$PYTHONNOUSERSITE" "$support_dir"
''')
        result = subprocess.run(['/bin/bash', str(script)],
                                env={**self.environment, **overrides},
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        values = result.stdout.decode().split('\0')
        self.assertEqual(values[-1], '')
        return values[:-1], resources / '../Frameworks/Python.framework/Versions/Current'

    def test_bundled_runtime_and_ca_are_selected_at_a_relocated_path(self):
        values, home = self.runtime_prefix()
        self.assertEqual(values, [str(home / 'bin/python3'), str(home),
                                  str(home / 'etc/pirouter-ca.pem'), '1', '1', str(self.support)])

    def test_explicit_interpreter_preserves_its_own_home_and_ca(self):
        selected = str(self.root / 'custom runtime/python3')
        values, _ = self.runtime_prefix(PIROUTER_PYTHON=selected,
                                        PYTHONHOME='/custom/python-home',
                                        SSL_CERT_FILE='/custom/ca.pem')
        self.assertEqual(values[:3], [selected, '/custom/python-home', '/custom/ca.pem'])
        self.assertEqual(values[3:5], ['1', '1'])

    def test_source_checkout_and_nonexecutable_bundle_leave_system_fallback_available(self):
        for bundled, executable in [(False, False), (True, False)]:
            with self.subTest(bundled=bundled, executable=executable):
                values, _ = self.runtime_prefix(bundled=bundled, executable=executable)
                self.assertEqual(values[:3], ['', '', ''])
                self.assertEqual(values[3:5], ['1', '1'])


if __name__ == '__main__':
    unittest.main()
