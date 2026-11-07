"""Validate portable Mac runtimes using fixtures, without running translated code."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'pirouter_runtime_bundle', ROOT / 'scripts/bundle-python-runtime-macos.py')
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class MacRuntimeArchitecture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="PiRouter's architecture & QA ")
        self.addCleanup(self.temporary.cleanup)
        self.framework = Path(self.temporary.name).resolve() / 'Moved App.app/Contents/Frameworks/Python.framework'
        self.version = self.framework / 'Versions/3.14'
        self.version.mkdir(parents=True)
        (self.framework / 'Versions/Current').symlink_to('3.14', target_is_directory=True)
        (self.framework / 'Python').symlink_to('Versions/Current/Python')
        (self.framework / 'Resources').symlink_to('Versions/Current/Resources', target_is_directory=True)
        self.main = self.version / 'Python'
        self.interpreter = self.version / 'bin/python3.14'
        self.nested = self.version / 'Resources/Python.app/Contents/MacOS/Python'
        self.extension = self.version / 'lib/python3.14/lib-dynload/_ssl.fixture.so'
        self.binaries = [self.main, self.interpreter, self.nested, self.extension]
        for binary in self.binaries:
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b'\xca\xfe\xba\xbe' + b'isolated Mach-O fixture')
            binary.chmod(0o755)
        (self.version / 'bin/python3').symlink_to('python3.14')
        self.info = self.version / 'Resources/Python.app/Contents/Info.plist'
        self.metadata = {
            'CFBundleExecutable': 'Python',
            'CFBundleIdentifier': 'org.python.python',
            'CFBundleName': 'Python',
            'CFBundleVersion': '3.14-fixture',
            'CustomFixtureSetting': {'preserve': True},
            'LSRequiresNativeExecution': True,
            'LSArchitecturePriority': ['arm64', 'x86_64'],
        }
        self.write_metadata(self.metadata)
        self.architectures = {}
        self.extra_dependencies = {}
        self.extra_rpaths = {}
        self.calls = []

    def write_metadata(self, metadata):
        self.info.write_bytes(plistlib.dumps(metadata))

    def fake_run(self, *arguments):
        self.calls.append(arguments)
        executable, option, filename = arguments
        binary = Path(filename).resolve()
        self.assertIn(binary, self.binaries)
        if executable == '/usr/bin/lipo' and option == '-archs':
            return self.architectures.get(binary, 'x86_64 arm64') + '\n'
        if executable == '/usr/bin/otool' and option == '-L':
            dependencies = ['/usr/lib/libSystem.B.dylib',
                            '/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation']
            if binary != self.main:
                dependencies.append('@loader_path/' + os.path.relpath(self.main, binary.parent))
            dependencies.extend(self.extra_dependencies.get(binary, []))
            return str(binary) + ':\n' + ''.join(
                '\t' + value + ' (compatibility version 1.0.0, current version 1.0.0)\n'
                for value in dependencies)
        if executable == '/usr/bin/otool' and option == '-l':
            return ''.join('Load command 1\n          cmd LC_RPATH\n      cmdsize 48\n'
                           '         path ' + value + ' (offset 12)\n'
                           for value in self.extra_rpaths.get(binary, []))
        raise AssertionError('Validation attempted an unexpected tool operation: ' + executable + ' ' + option)

    def validate(self):
        with mock.patch.object(RUNTIME, 'run', side_effect=self.fake_run):
            return RUNTIME.validate_framework(self.framework)

    def test_complete_universal_framework_validates_without_modification(self):
        before = {path: path.read_bytes() for path in [self.info, *self.binaries]}
        self.assertEqual(self.validate(), len(self.binaries))
        checked = {Path(call[2]).resolve() for call in self.calls if call[0] == '/usr/bin/lipo'}
        self.assertEqual(checked, set(self.binaries))
        self.assertTrue(all('architecture & QA' in call[2] for call in self.calls))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_intel_only_nested_helper_is_rejected_even_with_universal_main(self):
        self.architectures[self.nested] = 'x86_64'
        with self.assertRaisesRegex(RuntimeError, 'universal|architect|arm64'):
            self.validate()

    def test_each_binary_must_have_exactly_the_two_supported_architectures(self):
        for architectures in ('arm64', 'x86_64', 'arm64 x86_64 i386'):
            with self.subTest(architectures=architectures):
                self.architectures[self.extension] = architectures
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_missing_expected_runtime_executables_fail_despite_other_machos(self):
        for executable in [self.main, self.version / 'bin/python3', self.nested]:
            with self.subTest(executable=executable.relative_to(self.framework)):
                saved = executable.with_name(executable.name + '.temporarily-removed')
                executable.rename(saved)
                try:
                    with self.assertRaises(RuntimeError):
                        self.validate()
                finally:
                    saved.rename(executable)

    def test_no_macho_payload_cannot_be_accepted_as_a_runtime(self):
        for binary in self.binaries:
            binary.write_bytes(b'not a Mach-O executable\n')
        with self.assertRaises(RuntimeError):
            self.validate()
        self.assertEqual(self.calls, [])

    def test_required_interpreter_cannot_link_to_an_external_unchecked_binary(self):
        outside = self.framework.parent / 'Outside Python'
        outside.write_bytes(b'\xca\xfe\xba\xbe' + b'external interpreter fixture')
        outside.chmod(0o755)
        selected = self.version / 'bin/python3'
        selected.unlink()
        selected.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_external_dependencies_are_rejected_including_paths_with_spaces(self):
        for dependency in ('/opt/local/lib/private.dylib',
                           '/opt/External Runtime/private.dylib',
                           '/Library/Frameworks/Python.framework/Versions/3.14/Python',
                           '/System/LibraryUnexpected/private.dylib'):
            with self.subTest(dependency=dependency):
                self.extra_dependencies[self.extension] = [dependency]
                with self.assertRaisesRegex(RuntimeError, 'External|external|dependency'):
                    self.validate()

    def test_external_runtime_search_paths_are_rejected(self):
        self.extra_rpaths[self.nested] = ['/opt/Another Runtime/lib']
        with self.assertRaisesRegex(RuntimeError, 'External|external|search path'):
            self.validate()

    def test_old_cached_metadata_is_rejected_until_normalized(self):
        legacy = {key: value for key, value in self.metadata.items()
                  if key not in ('LSRequiresNativeExecution', 'LSArchitecturePriority')}
        self.write_metadata(legacy)
        before = self.info.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'native|arm64'):
            self.validate()
        self.assertEqual(self.info.read_bytes(), before)
        RUNTIME.normalize_native_execution(self.framework)
        normalized = plistlib.loads(self.info.read_bytes())
        self.assertTrue(normalized['LSRequiresNativeExecution'])
        self.assertEqual(normalized['LSArchitecturePriority'], ['arm64', 'x86_64'])
        self.assertEqual({key: value for key, value in normalized.items() if key in legacy}, legacy)
        self.assertEqual(self.validate(), len(self.binaries))

    def test_disabled_native_execution_or_intel_first_metadata_is_rejected(self):
        for updates in ({'LSRequiresNativeExecution': False},
                        {'LSArchitecturePriority': ['x86_64', 'arm64']},
                        {'LSArchitecturePriority': ['arm64']}):
            with self.subTest(updates=updates):
                self.write_metadata({**self.metadata, **updates})
                with self.assertRaises(RuntimeError):
                    self.validate()

    def test_validate_cli_checks_existing_runtime_without_build_or_network(self):
        with mock.patch('sys.argv', ['bundle-python-runtime-macos.py', '--validate', '--output', str(self.framework)]), \
                mock.patch.object(RUNTIME, 'run', side_effect=self.fake_run), \
                mock.patch.object(RUNTIME, 'bundle') as bundle, \
                mock.patch.object(RUNTIME.urllib.request, 'urlopen') as download, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            RUNTIME.main()
        bundle.assert_not_called()
        download.assert_not_called()
        self.assertIn('Validated', output.getvalue())
        self.assertEqual({Path(call[2]).resolve() for call in self.calls if call[0] == '/usr/bin/lipo'},
                         set(self.binaries))


if __name__ == '__main__':
    unittest.main()
