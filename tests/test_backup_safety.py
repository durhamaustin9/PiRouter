"""Exercise destructive cleanup only inside disposable directories with fake services."""
import json
import datetime
import os
import plistlib
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

MOCK = r'''#!/usr/bin/env python3
import datetime, json, os, pathlib, shutil, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['TEST_ROOT'])
with (root/'calls').open('a') as out:
    out.write(json.dumps([name, *args])+'\n')
if name == 'date': print(datetime.datetime.now(datetime.timezone.utc).isoformat())
elif name in ('flock', 'sleep'): pass
elif name in ('nice', 'ionice'): os.execvp(args[2], args[2:])
elif name == 'find':
    if '-cmin' in args: pass
    elif '-cnewer' in args:
        if os.environ.get('CHANGED') or (os.environ.get('CHANGED_AFTER_STOP') and (root/'stopped').exists()):
            print(root/'source'/'changed')
    else: sys.exit(subprocess.call(['/usr/bin/find', *args]))
elif name == 'smbstatus':
    if os.environ.get('BAD_LOCK_REPORT'): print('unavailable')
    else: print(json.dumps({'open_files': {str(root/'source'/'test.sparsebundle'/'bands'/'0'): {}} if os.environ.get('BUSY') else {}}))
elif name == 'mountpoint': sys.exit(1 if os.environ.get('UNMOUNTED') else 0)
elif name == 'systemctl':
    if args[0] == 'is-active': sys.exit(3 if (root/'stopped').exists() else 0)
    if args[0] == 'stop': (root/'stopped').touch()
    if args[0] == 'start': (root/'stopped').unlink(missing_ok=True)
elif name == 'restic':
    if 'backup' in args:
        time.sleep(.05)
        print(json.dumps({'message_type': 'summary', 'snapshot_id': 'a'*64, 'data_added': 100}))
        sys.exit(int(os.environ.get('BACKUP_EXIT', '0')))
    if 'check' in args and os.environ.get('VERIFY_FAIL'):
        print('pack checksum mismatch', file=sys.stderr); sys.exit(1)
    if 'cat' in args: print('{}')
elif name == 'rm':
    if '--one-file-system' in args and os.environ.get('REMOVE_FAIL'): sys.exit(1)
    for arg in args:
        if arg.startswith('-'): continue
        target = pathlib.Path(arg)
        # Every removal must stay in our disposable fixture or a mktemp file.
        if target.is_dir():
            assert target.is_relative_to(root), target
            shutil.rmtree(target)
        elif target.exists():
            assert target.is_relative_to(root) or target.name.startswith('tmp.'), target
            target.unlink()
'''


class ArchiveSafety(unittest.TestCase):
    def run_archive(self, **overrides):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / 'source'
        bundle = source / 'test.sparsebundle'
        (bundle / 'bands').mkdir(parents=True)
        (bundle / 'bands' / '0').write_bytes(b'backup')
        (bundle / 'com.apple.TimeMachine.Results.plist').write_bytes(plistlib.dumps({'RESULT': int(overrides.pop('TM_RESULT', '0'))}))
        (source / 'unrelated').mkdir()
        (source / 'unrelated' / 'keep').write_text('keep')
        (source / '.share-support').touch()
        export_state = overrides.pop('EXPORT_STATE', None)
        portable = root / 'portable.json'
        if export_state and export_state != 'missing':
            portable.write_text(json.dumps({
                'state': 'error' if export_state == 'error' else 'success',
                'verified': export_state != 'unverified',
                'lastSuccessAt': '2000-01-01T00:00:00Z' if export_state == 'stale' else datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }))
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        for name in ('date', 'flock', 'sleep', 'nice', 'ionice', 'find', 'smbstatus', 'mountpoint', 'systemctl', 'restic', 'rm'):
            executable = bin_dir / name
            executable.write_text(MOCK)
            executable.chmod(0o755)
        config = root / 'config'
        config.write_text('\n'.join(f'{key}={shlex.quote(str(value))}' for key, value in {
            'TIME_MACHINE_SOURCE': source, 'ARCHIVE_REPOSITORY': 'test',
            'ARCHIVE_SSH_TARGET': 'test', 'ARCHIVE_SSH_KEY': root/'key',
            'RESTIC_PASSWORD_FILE': root/'password', 'RESTIC_BACKUP_HOST': 'test',
            'RESTIC_CACHE_DIR': root/'cache', 'REMOVE_LOCAL_AFTER_ARCHIVE': 'true',
            **({'PORTABLE_EXPORT_STATUS_FILE': portable} if export_state else {}),
        }.items()))
        reset = overrides.pop('RESET', None)
        result = subprocess.run(['bash', str(ROOT/'scripts/archive-backup.sh'), *(['reset'] if reset else [])], env={
            **os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'TEST_ROOT': str(root),
            'ARCHIVE_BACKUP_CONFIG': str(config), 'ARCHIVE_STATUS_FILE': str(root/'status.json'),
            'ARCHIVE_RUNTIME_DIR': str(root/'run'), 'ARCHIVE_LOCK_FILE': str(root/'lock'),
            'TMPDIR': str(root), **overrides,
        }, capture_output=True, text=True, timeout=30)
        status = json.loads((root/'status.json').read_text())
        calls = [json.loads(line) for line in (root/'calls').read_text().splitlines()]
        return root, bundle, result, status, calls

    def test_verified_upload_removes_only_bundle_and_restarts_share(self):
        root, bundle, result, status, calls = self.run_archive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(bundle.exists())
        self.assertTrue((root/'source/unrelated/keep').exists())
        self.assertTrue((root/'source/.share-support').exists())
        self.assertFalse((root/'stopped').exists())
        self.assertTrue(status['lastCleanupAt'])
        self.assertTrue(any('check' in call and '--read-data' in call for call in calls))

    def test_failure_never_removes_local_backup(self):
        for scenario in ({'VERIFY_FAIL': '1'}, {'BACKUP_EXIT': '3'}, {'BUSY': '1'}, {'BAD_LOCK_REPORT': '1'}, {'UNMOUNTED': '1'}, {'CHANGED': '1'}, {'CHANGED_AFTER_STOP': '1'}, {'TM_RESULT': '70'}):
            with self.subTest(scenario=scenario):
                root, bundle, result, status, calls = self.run_archive(**scenario)
                self.assertTrue(bundle.exists(), (scenario, result.stderr))
                self.assertFalse((root/'stopped').exists(), (scenario, result.stderr))
                self.assertFalse(status.get('lastCleanupAt'))

    def test_removal_failure_restores_smb(self):
        root, bundle, result, status, calls = self.run_archive(REMOVE_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(bundle.exists())
        self.assertFalse((root/'stopped').exists())
        self.assertEqual(status['state'], 'error')
        self.assertIn('Could not remove', status['errorDetail'])

    def test_export_receipt_must_be_verified_and_newer_than_the_source(self):
        for state in ('missing', 'error', 'unverified', 'stale', 'verified'):
            with self.subTest(state=state):
                root, bundle, result, status, calls = self.run_archive(EXPORT_STATE=state)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(bundle.exists(), state != 'verified')

    def test_explicit_reset_archives_and_verifies_an_interrupted_backup(self):
        root, bundle, result, status, calls = self.run_archive(TM_RESULT='70', EXPORT_STATE='error', RESET='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(bundle.exists())
        self.assertTrue(any('check' in call and '--read-data' in call for call in calls))


class PortableValidation(unittest.TestCase):
    def test_exporter_waits_for_mount_completion_and_rejects_a_late_mount_failure(self):
        script = (ROOT/'scripts/portable-export-macos.sh').read_text()
        function = script[script.index('latest_snapshot() {'):script.index('\nif ! api_get; then')]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root/'2000-01-01-120000.backup'
            snapshot.mkdir()
            mock = root/'tmutil'
            mock.write_text('''#!/bin/bash
if [[ "$1" == status ]]; then echo 'Running = 0'; exit; fi
printf '%s\\n' "$MOCK_SNAPSHOT"
sleep 0.2
touch "$MOCK_SNAPSHOT/mount-finished"
exit "$MOCK_MOUNT_RESULT"
''')
            mock.chmod(0o755)
            harness = function.replace('/usr/bin/tmutil', shlex.quote(str(mock))) + '\nlatest_snapshot\nexit $?\n'
            for code in ('0', '1'):
                (snapshot/'mount-finished').unlink(missing_ok=True)
                result = subprocess.run(['/bin/bash', '-c', harness], env={
                    **os.environ, 'MOCK_SNAPSHOT': str(snapshot), 'MOCK_MOUNT_RESULT': code,
                    'TMPDIR': str(root),
                }, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if code == '0' else 78, result.stderr)
                self.assertTrue((snapshot/'mount-finished').exists())

    def test_empty_data_volume_is_rejected_even_when_another_volume_has_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'Other').mkdir(); (root/'Other/file').write_text('backup')
            (root/'Macintosh HD - Data').mkdir()
            result = subprocess.run(['python3', str(ROOT/'scripts/validate-portable-snapshot.py'), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Macintosh HD - Data', result.stderr)
            (root/'Macintosh HD - Data/Users').mkdir()
            (root/'Macintosh HD - Data/Users/file').write_text('user data')
            result = subprocess.run(['python3', str(ROOT/'scripts/validate-portable-snapshot.py'), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)), 2)


if __name__ == '__main__':
    unittest.main()
