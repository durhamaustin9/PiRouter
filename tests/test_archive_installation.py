"""Probe repository setup using disposable files and fake privileged commands."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'scripts/install-archive-backup.sh').read_text()
PREPARE = SCRIPT[SCRIPT.index('prepare_archive_repository() {'):
                 SCRIPT.index('\nif [[ -z "$archive_ssh_target"')]
SETTINGS = SCRIPT[SCRIPT.index('existing_setting() {'):
                  SCRIPT.index('\n# Restic reserves')]
RESOLVE_DESTINATION = SCRIPT[SCRIPT.index('\nif [[ -z "$archive_repository" ]]; then'):
                             SCRIPT.index('\nif [[ -z "$archive_ssh_target" ]]; then\n  echo')]

RESTIC = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
root=pathlib.Path(os.environ['TEST_ROOT'])
with (root/'calls').open('a') as handle: handle.write(json.dumps(['restic',*args])+'\n')
if args[-2:]==['cat','config']:
    code=int(os.environ.get('PROBE_EXIT','0'))
    if code: print('test repository probe failure',file=sys.stderr)
    else: print('{"id":"disposable-fixture"}')
    sys.exit(code)
if args[-1]=='init':
    code=int(os.environ.get('INIT_EXIT','0'))
    if code: sys.exit(code)
    repository=root/'repository'
    repository.mkdir(exist_ok=True)
    (repository/'config').write_text('new fixture repository')
    sys.exit(0)
raise AssertionError(args)
'''

SSH = r'''#!/usr/bin/env python3
import json,os,pathlib,subprocess,sys
root=pathlib.Path(os.environ['TEST_ROOT'])
with (root/'calls').open('a') as handle: handle.write(json.dumps(['ssh',*sys.argv[1:]])+'\n')
if os.environ.get('SSH_EXIT'): sys.exit(int(os.environ['SSH_EXIT']))
sys.exit(subprocess.call(['/bin/sh','-c',sys.argv[-1]]))
'''


class ArchiveInstallation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pirouter-install-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        binaries = self.root / 'bin'
        binaries.mkdir()
        for name, text in [('restic', RESTIC), ('ssh', SSH), ('sudo', '#!/bin/sh\nexec "$@"\n')]:
            executable = binaries / name
            executable.write_text(text)
            executable.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'], TEST_ROOT=str(self.root))

    def prepare(self, **overrides):
        repository = overrides.pop('REPOSITORY', 'sftp:backup@example.invalid:' + str(self.root / 'repository'))
        setup = '\n'.join(key + '=' + shlex.quote(value) for key, value in {
            'password_file': str(self.root / 'password'),
            'ssh_key_file': str(self.root / 'key'),
            'archive_repository': repository,
            'archive_ssh_target': 'backup@example.invalid',
            'sftp_command': 'fake test transport',
        }.items())
        result = subprocess.run(['/bin/bash', '-eu', '-c', PREPARE + '\n' + setup + '\nprepare_archive_repository\n'],
                                env=dict(self.env, **overrides), capture_output=True, text=True, timeout=10)
        calls_file = self.root / 'calls'
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return result, calls

    def test_existing_repository_is_opened_without_initialization(self):
        result, calls = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ['cat', 'config'])
        self.assertIn('--no-cache', calls[0])
        self.assertIn('--no-lock', calls[0])

    def test_fresh_install_uses_the_dedicated_time_machine_share(self):
        setup = 'config_dir=/etc/pirouter\nenvironment_file=' + shlex.quote(str(self.root / 'missing.env')) + '\n'
        environment = dict(self.env, ARCHIVE_SSH_TARGET='backup@example.invalid')
        for key in ('ARCHIVE_REPOSITORY', 'ARCHIVE_PATH'):
            environment.pop(key, None)
        script = setup + SETTINGS + RESOLVE_DESTINATION + '\nprintf \'%s\' "$archive_repository"\n'
        result = subprocess.run(['/bin/bash', '-eu', '-c', script], env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         'sftp:backup@example.invalid:/mnt/user/time-machine-austin/PiRouter-TimeMachine.restic')

    def test_only_explicit_missing_repository_initializes_after_empty_path_check(self):
        result, calls = self.prepare(PROBE_EXIT='10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ['restic', 'ssh', 'restic'])
        self.assertEqual(calls[-1][-1], 'init')
        self.assertTrue((self.root / 'repository' / 'config').exists())

    def test_empty_existing_directory_can_be_initialized(self):
        (self.root / 'repository').mkdir()
        result, calls = self.prepare(PROBE_EXIT='10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1][-1], 'init')

    def test_password_network_lock_and_unknown_failures_never_initialize(self):
        for code in ('1', '2', '11', '12', '42'):
            with self.subTest(code=code):
                (self.root / 'calls').unlink(missing_ok=True)
                result, calls = self.prepare(PROBE_EXIT=code)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)
                self.assertIn('Initialization was not attempted', result.stderr)
                self.assertFalse((self.root / 'repository').exists())

    def test_missing_config_in_a_nonempty_repository_preserves_existing_data(self):
        repository = self.root / 'repository'
        repository.mkdir()
        (repository / 'data-pack').write_bytes(b'existing archive data')
        result, calls = self.prepare(PROBE_EXIT='10')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[-1] == 'init' for call in calls))
        self.assertEqual((repository / 'data-pack').read_bytes(), b'existing archive data')

    def test_symlinked_repository_and_failed_remote_probe_never_initialize(self):
        real = self.root / 'actual-directory'
        real.mkdir()
        (self.root / 'repository').symlink_to(real, target_is_directory=True)
        result, calls = self.prepare(PROBE_EXIT='10')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[-1] == 'init' for call in calls))
        (self.root / 'calls').unlink()
        (self.root / 'repository').unlink()
        result, calls = self.prepare(PROBE_EXIT='10', SSH_EXIT='255')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[-1] == 'init' for call in calls))

    def test_wrong_target_and_traversal_paths_never_initialize(self):
        for repository in ('sftp:other@example.invalid:/archive',
                           'sftp:backup@example.invalid:/archive/../elsewhere'):
            with self.subTest(repository=repository):
                (self.root / 'calls').unlink(missing_ok=True)
                result, calls = self.prepare(PROBE_EXIT='10', REPOSITORY=repository)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)

    def test_failed_initialization_is_not_reported_as_success(self):
        result, calls = self.prepare(PROBE_EXIT='10', INIT_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][-1], 'init')

    def test_reinstallation_preserves_destination_source_and_source_write_guards(self):
        config = self.root / 'archive.env'
        config.write_text('''TIME_MACHINE_SOURCE=/srv/backup-volume
ARCHIVE_SSH_TARGET=backup@example.invalid
ARCHIVE_REPOSITORY=sftp:backup@example.invalid:/archive/existing.restic
QUIET_MINUTES=60
COPY_GUARD_SECONDS=15
REMOVE_LOCAL_AFTER_ARCHIVE=false
PORTABLE_EXPORT_STATUS_FILE=/srv/dashboard/export.json
''')
        setup = 'config_dir=/etc/pirouter\nenvironment_file=' + shlex.quote(str(config)) + '\n'
        show = '\nprintf "%s\\n" "$time_machine_source" "$archive_ssh_target" "$archive_repository" "$quiet_minutes" "$copy_guard_seconds" "$remove_local_after_archive" "$portable_export_status_file"\n'
        environment = dict(self.env)
        for key in ('TIME_MACHINE_SOURCE', 'ARCHIVE_SSH_TARGET', 'ARCHIVE_REPOSITORY',
                    'ARCHIVE_PATH', 'QUIET_MINUTES', 'COPY_GUARD_SECONDS',
                    'REMOVE_LOCAL_AFTER_ARCHIVE', 'PORTABLE_EXPORT_STATUS_FILE'):
            environment.pop(key, None)
        result = subprocess.run(['/bin/bash', '-eu', '-c', setup + SETTINGS + show],
                                env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            '/srv/backup-volume', 'backup@example.invalid',
            'sftp:backup@example.invalid:/archive/existing.restic', '60', '15',
            'false', '/srv/dashboard/export.json',
        ])

    def test_custom_retention_backup_identity_and_credential_paths_survive_reinstallation(self):
        config = self.root / 'archive.env'
        config.write_text('''ARCHIVE_SSH_KEY=/etc/backup-credentials/archive-key
RESTIC_PASSWORD_FILE=/etc/backup-credentials/recovery-password
RESTIC_BACKUP_HOST=backup-appliance
RESTIC_BACKUP_TAG=household-backups
KEEP_DAILY=30
KEEP_WEEKLY=16
KEEP_MONTHLY=24
''')
        setup = 'config_dir=/etc/pirouter\nenvironment_file=' + shlex.quote(str(config)) + '\n'
        names = ['ssh_key_file', 'password_file', 'backup_host', 'backup_tag',
                 'keep_daily', 'keep_weekly', 'keep_monthly']
        show = '\nprintf "%s\\n" ' + ' '.join('"$' + name + '"' for name in names) + '\n'
        environment = dict(self.env)
        for key in ('ARCHIVE_SSH_KEY', 'RESTIC_PASSWORD_FILE', 'RESTIC_BACKUP_HOST',
                    'RESTIC_BACKUP_TAG', 'KEEP_DAILY', 'KEEP_WEEKLY', 'KEEP_MONTHLY'):
            environment.pop(key, None)
        result = subprocess.run(['/bin/bash', '-eu', '-c', setup + SETTINGS + show],
                                env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            '/etc/backup-credentials/archive-key',
            '/etc/backup-credentials/recovery-password',
            'backup-appliance', 'household-backups', '30', '16', '24',
        ])


if __name__ == '__main__':
    unittest.main()
