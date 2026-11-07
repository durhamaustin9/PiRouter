"""Exercise snapshot pinning and lock recovery without mounting a real backup."""
import os
import json
import datetime
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

CURL = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
root=pathlib.Path(os.environ['WRAPPER_TEST_ROOT'])
if (root/'offline').exists(): sys.exit(22)
url=next(arg for arg in args if arg.startswith('http'))
output=pathlib.Path(args[args.index('-o')+1])
with (root/'events').open('a') as out:
    out.write(('archive-trigger' if url.endswith('/api/archive-backup') else 'api')+'\n')
if '--data-binary' in args:
    with (root/'updates').open('a') as out: out.write(args[args.index('--data-binary')+1]+'\n')
if url.endswith('/api/archive-backup'): output.write_text('{"message":"queued"}')
else:
    status=json.loads((root/'status.json').read_text())
    activation=root/'activate-after-api'
    if activation.exists() and (root/'events').read_text().splitlines().count('api')>int(activation.read_text()):
        status['archiveCoordination']['serviceActive']=True
    output.write_text(json.dumps(status))
'''

PLUTIL = r'''#!/usr/bin/env python3
import json,sys
try:
    value=json.load(open(sys.argv[-1]))
    for key in sys.argv[2].split('.'): value=value[key]
    if value is None: print('null')
    elif isinstance(value,bool): print(str(value).lower())
    else: print(value)
except (OSError,ValueError,KeyError,TypeError): sys.exit(1)
'''

MOUNTS = r'''import json,os,pathlib,sys
root=pathlib.Path(os.environ['WRAPPER_TEST_ROOT'])
args=sys.argv[1:]
receipt=pathlib.Path(args[args.index('--statefile')+1])
with (root/'events').open('a') as out: out.write('helper-'+args[0]+'\n')
if args[0]=='capture':
    if '--if-mounted' in args and not (root/'attached').exists(): sys.exit(0)
    receipt.write_text('{"fixture":true}')
elif args[0]=='release':
    if (root/'busy').exists(): sys.exit(75)
    if os.getcwd()!='/': raise AssertionError('Release occurred with cwd inside source')
    if (root/'require-child-stopped').exists() and not (root/'child-stopped').exists():
        raise AssertionError('Release occurred before waiting for the transfer child')
    receipt.unlink(missing_ok=True)
    (root/'attached').unlink(missing_ok=True)
    if (root/'next-time-machine-busy').exists(): (root/'busy').touch()
'''

TMUTIL = r'''#!/bin/bash
if [[ "$1" == status ]]; then
  if [[ -e "$WRAPPER_TEST_ROOT/busy" ]]; then echo 'Running = 1'; else echo 'Running = 0'; fi
  exit 0
fi
printf 'tmutil-mount\n' >> "$WRAPPER_TEST_ROOT/events"
touch "$WRAPPER_TEST_ROOT/attached"
printf '%s\n' "$WRAPPER_TEST_ROOT/2000-01-01-120000.backup"
exit 0
'''


class PortableWrapper(unittest.TestCase):
    def test_an_unfinished_job_selects_its_original_snapshot(self):
        script = (ROOT / 'scripts/portable-export-macos.sh').read_text()
        function = script[script.index('latest_snapshot() {'):script.index('\nif ! api_get; then')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / '2000-01-01-120000.backup'
            newer = root / '2000-01-02-120000.backup'
            original.mkdir()
            newer.mkdir()
            mock = root / 'tmutil'
            mock.write_text('''#!/bin/bash
if [[ "$1" == status ]]; then echo 'Running = 0'; exit; fi
printf '%s\\n%s\\n' "$NEWER_SNAPSHOT" "$ORIGINAL_SNAPSHOT"
exit 0
''')
            mock.chmod(0o755)
            harness = function.replace('/usr/bin/tmutil', shlex.quote(str(mock)))
            harness += '\npinned_snapshot=2000-01-01-120000\npinned_path=""\nlatest_snapshot\nresult=$?\nprintf "%s\\n" "$snapshot_path"\nexit "$result"\n'
            result = subprocess.run(['/bin/bash', '-c', harness], env={
                **os.environ, 'ORIGINAL_SNAPSHOT': str(original),
                'NEWER_SNAPSHOT': str(newer), 'TMPDIR': str(root),
            }, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(original))

    def test_process_lock_survives_exec_and_recovers_after_process_death(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / 'started'
            sleeping = root / 'sleeping.sh'
            sleeping.write_text('printf started > "$1"\nexec sleep 30\n')
            command = [sys.executable, str(ROOT / 'scripts/portable-export-lock.py'),
                       str(root / 'export.lock'), str(sleeping), str(marker)]
            first = subprocess.Popen(command)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                started = time.monotonic()
                second = subprocess.run(command, capture_output=True, timeout=3)
                self.assertEqual(second.returncode, 0)
                self.assertLess(time.monotonic() - started, 2)
                self.assertIsNone(first.poll())
            finally:
                first.terminate()
                first.wait(timeout=5)
            script = root / 'next.sh'
            script.write_text('exit 37\n')
            resumed = subprocess.run(command[:3] + [str(script)], timeout=3)
            self.assertEqual(resumed.returncode, 37)


class ArchiveHandoff(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pirouter-wrapper-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        binaries = self.root / 'bin'
        binaries.mkdir()
        for name, text in [('curl', CURL), ('plutil', PLUTIL), ('tmutil', TMUTIL),
                           ('uname', '#!/bin/sh\nprintf "Darwin\\n"\n')]:
            executable = binaries / name
            executable.write_text(text)
            executable.chmod(0o755)
        script = (ROOT / 'scripts/portable-export-macos.sh').read_text()
        for command in ('curl', 'plutil', 'tmutil'):
            script = script.replace('/usr/bin/' + command, shlex.quote(str(binaries / command)))
        self.wrapper = self.root / 'portable-export-macos.sh'
        self.wrapper.write_text(script)
        (self.root / 'portable-export-mounts.py').write_text(MOUNTS)
        (self.root / 'resumable-portable-export.py').write_text(r'''import fcntl,json,os,pathlib,sqlite3,sys,time
root=pathlib.Path(os.environ['WRAPPER_TEST_ROOT'])
with (root/'events').open('a') as out: out.write('engine\n')
counter=root/'engine-count'
number=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(number))
(root/'engine-args').write_text(json.dumps(sys.argv[1:]))
with (root/'engine-requests').open('a') as out: out.write(os.environ.get('PIROUTER_REQUEST_ID','')+'\n')
if (root/'verify-global-lock').exists():
    os.fstat(9)
    with (root/'support/export.lock').open('a+') as probe:
        try: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            with (root/'lock-checks').open('a') as out: out.write('held\n')
        else: raise AssertionError('Global exporter lock was lost across continuation')
if (root/'engine-sleep-seconds').exists(): time.sleep(float((root/'engine-sleep-seconds').read_text()))
if (root/'advance-progress').exists():
    args=sys.argv[1:]
    state=pathlib.Path(args[args.index('--state-dir')+1])
    state.mkdir(exist_ok=True)
    snapshot=args[args.index('--snapshot')+1]
    with sqlite3.connect(str(state/(snapshot+'.sqlite3'))) as db:
        db.execute('CREATE TABLE IF NOT EXISTS job(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        for key,amount in [('bytes_copied',int((root/'advance-progress').read_text())),('files_copied',1)]:
            old=db.execute('SELECT value FROM job WHERE key=?',(key,)).fetchone()
            db.execute('INSERT OR REPLACE INTO job VALUES(?,?)',(key,str((int(old[0]) if old else 0)+amount)))
if number==1:
    if (root/'archive-after-engine').exists():
        filename=root/'status.json';status=json.loads(filename.read_text())
        status['archiveCoordination']['serviceActive']=True
        filename.write_text(json.dumps(status))
    if (root/'offline-after-engine').exists(): (root/'offline').touch()
    if (root/'busy-after-engine').exists(): (root/'busy').touch()
sequence=root/'engine-results'
results=list(map(int,sequence.read_text().split())) if sequence.exists() else []
result=results[number-1] if number<=len(results) else 75
if result==0:
    filename=root/'status.json';status=json.loads(filename.read_text())
    status.update(state='success',lastSnapshotName=sys.argv[sys.argv.index('--snapshot')+1],requestId=None)
    filename.write_text(json.dumps(status))
sys.exit(result)
''')
        (self.root / 'key').write_text('disposable test key placeholder')
        self.support = self.root / 'support'
        self.support.mkdir()
        self.config = self.support / 'portable-export.env'
        self.config.write_text('\n'.join(key + '=' + shlex.quote(value) for key, value in {
            'PIROUTER_URL': 'http://example.invalid', 'PIROUTER_TOKEN': 'test-pairing',
            'EXPORT_HOST': 'backup@example.invalid', 'EXPORT_ROOT': '/mnt/user/test',
            'EXPORT_SSH_KEY': str(self.root / 'key'),
            'CONTINUOUS_EXPORT': 'false',
        }.items()))
        self.source = self.root / '2000-01-01-120000.backup'
        (self.source / 'Macintosh HD - Data').mkdir(parents=True)
        (self.source / 'Macintosh HD - Data' / 'file').write_bytes(b'backup user data')
        self.status = {
            'state': 'paused', 'requestId': '11111111-1111-4111-8111-111111111111',
            'archiveCoordination': {
                'configured': True, 'state': 'waiting',
                'lastAttemptAt': '2000-01-03T10:00:00-07:00',
                'lastSuccessAt': None, 'lastSnapshotId': None,
                'serviceActive': False, 'needsArchive': True,
            },
        }
        self.save_status()
        self.environment = dict(os.environ, WRAPPER_TEST_ROOT=str(self.root),
                                PIROUTER_EXPORT_LOCKED='1',
                                PIROUTER_PORTABLE_EXPORT_CONFIG=str(self.config),
                                PATH=str(binaries) + ':' + os.environ['PATH'])

    def save_status(self):
        (self.root / 'status.json').write_text(json.dumps(self.status))

    def run_wrapper(self):
        return subprocess.run(['/bin/bash', str(self.wrapper)], env=self.environment,
                              capture_output=True, text=True, timeout=15)

    def events(self):
        filename = self.root / 'events'
        return filename.read_text().splitlines() if filename.exists() else []

    def pin_job(self):
        (self.support / 'portable-export-job.json').write_text(json.dumps({
            'snapshotName': '2000-01-01-120000', 'sourcePath': str(self.source),
        }))

    def completed_archive(self, identity='a' * 64, succeeded='2000-01-07T15:23:57-07:00'):
        self.status['archiveCoordination'].update(
            lastSnapshotId=identity, lastSuccessAt=succeeded, needsArchive=True,
            lastAttemptAt='2000-01-07T15:45:00-07:00', state='waiting')
        self.save_status()

    def folder_turn(self):
        return json.loads((self.support / 'folder-export-turn.json').read_text())

    def continuous_job(self):
        self.config.write_text(self.config.read_text().replace('CONTINUOUS_EXPORT=false', 'CONTINUOUS_EXPORT=true'))
        self.pin_job()
        self.completed_archive()

    def test_active_archive_prevents_any_mount_or_engine(self):
        self.pin_job()
        self.status['archiveCoordination']['serviceActive'] = True
        self.status['archiveCoordination']['state'] = 'success'
        self.save_status()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('tmutil-mount', self.events())
        self.assertNotIn('engine', self.events())
        update = json.loads((self.root / 'updates').read_text().splitlines()[-1])
        self.assertEqual(update['message'], 'Pi is archiving; folder progress is saved')
        self.assertEqual(update['activeSnapshotName'], '2000-01-01-120000')

    def test_already_archived_idle_source_does_not_trigger_another_archive(self):
        self.status['archiveCoordination'].update(state='success', needsArchive=False)
        self.save_status()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('engine', self.events())
        self.assertIn('helper-release', self.events())
        self.assertNotIn('archive-trigger', self.events())
        self.assertFalse((self.support / 'archive-handoff.json').exists())

    def test_archive_starting_just_before_mount_is_rechecked(self):
        self.status['archiveCoordination']['needsArchive'] = False
        self.save_status()
        (self.root / 'activate-after-api').write_text('3')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('tmutil-mount', self.events())
        self.assertNotIn('engine', self.events())

    def test_unknown_source_coordination_fails_closed_and_retries(self):
        self.status['archiveCoordination']['needsArchive'] = None
        self.save_status()
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertNotIn('tmutil-mount', self.events())
        self.assertNotIn('engine', self.events())
        self.status['archiveCoordination']['needsArchive'] = False
        self.save_status()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('engine', self.events())

    def test_handoff_keeps_image_closed_through_new_waiting_attempts(self):
        first = self.run_wrapper()
        self.assertEqual(first.returncode, 0, first.stderr)
        handoff = self.support / 'archive-handoff.json'
        self.assertTrue(handoff.exists())
        self.assertIn('archive-trigger', self.events())
        for attempt in ('2000-01-03T10:05:00-07:00', '2000-01-03T10:10:00-07:00'):
            self.status['archiveCoordination']['lastAttemptAt'] = attempt
            self.save_status()
            self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertNotIn('tmutil-mount', self.events())
        self.assertNotIn('engine', self.events())
        self.assertTrue(handoff.exists())
        self.status['archiveCoordination'].update(state='success', needsArchive=False,
                                                lastAttemptAt='2000-01-03T10:20:00-07:00')
        self.save_status()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('tmutil-mount'), 1)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)

    def test_existing_attachment_releases_before_archive_trigger_even_if_pi_offline(self):
        self.pin_job()
        (self.root / 'attached').touch()
        (self.root / 'offline').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 1)
        self.assertIn('helper-release', self.events())
        self.assertFalse((self.root / 'attached').exists())
        handoff = json.loads((self.support / 'archive-handoff.json').read_text())
        self.assertFalse(handoff['baselineKnown'])
        (self.root / 'offline').unlink()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('tmutil-mount', self.events())
        self.assertTrue(json.loads((self.support / 'archive-handoff.json').read_text())['baselineKnown'])

    def test_busy_image_is_retained_without_triggering_or_reopening(self):
        self.pin_job()
        (self.root / 'attached').touch()
        (self.root / 'busy').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'attached').exists())
        self.assertTrue((self.support / 'portable-export-mount.json').exists())
        self.assertNotIn('archive-trigger', self.events())
        self.assertNotIn('tmutil-mount', self.events())
        self.assertNotIn('engine', self.events())

    def test_completed_archive_recovers_legacy_handoff_despite_newer_bundle_ctimes(self):
        self.pin_job()
        self.completed_archive()
        (self.support / 'archive-handoff.json').write_text(json.dumps({
            'baselineKnown': True, 'baselineAttemptAt': '2000-01-07T15:23:22-07:00',
            'createdAt': '2000-01-07T15:33:50-07:00',
        }))
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('engine', self.events())
        self.assertNotIn('archive-trigger', self.events())
        self.assertFalse((self.support / 'archive-handoff.json').exists())
        grant = self.folder_turn()
        self.assertEqual(grant['archiveSnapshotId'], 'a' * 64)
        self.assertEqual(grant['remainingSeconds'], 3600)
        self.assertFalse(grant['consumed'])

    def test_repeated_preupload_failures_keep_credit_without_resetting_window(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        first = self.folder_turn()
        for unused in range(2):
            result = self.run_wrapper()
            self.assertEqual(result.returncode, 0, result.stderr)
        current = self.folder_turn()
        self.assertEqual(current['grantedAt'], first['grantedAt'])
        self.assertEqual(current['expiresAt'], first['expiresAt'])
        self.assertEqual(current['remainingSeconds'], 3600)
        self.assertEqual(self.events().count('engine'), 3)
        self.assertNotIn('archive-trigger', self.events())

    def test_useful_sender_time_is_debited_across_bounded_sessions(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        script = (ROOT / 'scripts/portable-export-macos.sh').read_text()
        functions = script[script.index('finish_folder_turn() {'):script.index('\nwrite_archive_handoff() {')]
        filename = self.support / 'folder-export-turn.json'
        setup = 'folder_turn_file=' + shlex.quote(str(filename)) + '''
folder_turn_active=true
turn_progress_before="0 0"
turn_progress_started="$(/usr/bin/python3 -c 'import time; print(time.time()-600)')"
read_transfer_progress() { printf '1024 1\n'; }
finish_folder_turn 75
'''
        result = subprocess.run(['/bin/bash', '-u', '-c', functions + '\n' + setup],
                                env=self.environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(self.folder_turn()['remainingSeconds'], 2995)
        self.assertLessEqual(self.folder_turn()['remainingSeconds'], 3000)
        self.assertFalse(self.folder_turn()['consumed'])
        # A later bounded upload retains the debit instead of refreshing it.
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertLessEqual(self.folder_turn()['remainingSeconds'], 3000)

    def test_actual_cross_process_sender_elapsed_is_debited(self):
        self.completed_archive()
        (self.root / 'engine-sleep-seconds').write_text('1.2')
        (self.root / 'advance-progress').write_text('1024')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        grant = self.folder_turn()
        # Exercise native /usr/bin/python3 clocks across the same separate
        # processes as production, rather than constructing synthetic offsets.
        self.assertGreaterEqual(grant['usefulSecondsUsed'], 2)
        self.assertLessEqual(grant['usefulSecondsUsed'], 10)
        self.assertEqual(grant['remainingSeconds'], 3600 - grant['usefulSecondsUsed'])
        self.assertFalse(grant['consumed'])

    def test_clock_jumps_cannot_charge_negative_time_or_exceed_session_cap(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        script = (ROOT / 'scripts/portable-export-macos.sh').read_text()
        functions = script[script.index('finish_folder_turn() {'):script.index('\nwrite_archive_handoff() {')]
        filename = self.support / 'folder-export-turn.json'
        original = self.folder_turn()
        for offset in (-86400, 86400):
            with self.subTest(clock_offset=offset):
                filename.write_text(json.dumps(original))
                setup = 'folder_turn_file=' + shlex.quote(str(filename)) + '\n'
                setup += 'clock_offset=' + str(offset) + '''
folder_turn_active=true
turn_progress_before="0 0"
export_session_seconds=100
turn_progress_started="$(/usr/bin/python3 -c 'import sys,time; print(time.time()+float(sys.argv[1]))' "$clock_offset")"
read_transfer_progress() { printf '1024 1\n'; }
finish_folder_turn 75
'''
                result = subprocess.run(['/bin/bash', '-u', '-c', functions + '\n' + setup],
                                        env=self.environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.folder_turn()['usefulSecondsUsed'], 100)
                self.assertEqual(self.folder_turn()['remainingSeconds'], 3500)
        self.assertFalse(self.folder_turn()['consumed'])

    def test_exhausted_same_archive_cannot_regrant_but_new_archive_can(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        filename = self.support / 'folder-export-turn.json'
        grant = self.folder_turn()
        grant.update(consumed=True, remainingSeconds=0)
        filename.write_text(json.dumps(grant))
        previous_engines = self.events().count('engine')
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual(self.events().count('engine'), previous_engines)
        self.assertIn('archive-trigger', self.events())
        self.completed_archive('b' * 64, '2000-01-07T16:23:57-07:00')
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual(self.events().count('engine'), previous_engines + 1)
        self.assertEqual(self.folder_turn()['remainingSeconds'], 3600)

    def test_six_hour_expiry_yields_to_archive_even_after_zero_progress_failures(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        filename = self.support / 'folder-export-turn.json'
        grant = self.folder_turn()
        expired = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        grant['expiresAt'] = expired.isoformat()
        filename.write_text(json.dumps(grant))
        previous_engines = self.events().count('engine')
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual(self.events().count('engine'), previous_engines)
        self.assertIn('archive-trigger', self.events())

    def test_remaining_window_caps_the_next_sender_session(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        filename = self.support / 'folder-export-turn.json'
        grant = self.folder_turn()
        grant['remainingSeconds'] = 100
        filename.write_text(json.dumps(grant))
        self.assertEqual(self.run_wrapper().returncode, 0)
        arguments = json.loads((self.root / 'engine-args').read_text())
        self.assertEqual(arguments[arguments.index('--session-seconds') + 1], '100')
        self.assertEqual(self.folder_turn()['remainingSeconds'], 100)

    def test_normal_timed_checkpoint_continues_under_same_request_after_release(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('76 75')
        (self.root / 'advance-progress').write_text('1024')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual(events.count('engine'), 2)
        first, second = [i for i, event in enumerate(events) if event == 'engine']
        self.assertIn('helper-release', events[first + 1:second])
        self.assertIn('api', events[first + 1:second])
        self.assertIn('helper-capture', events[first + 1:second])
        self.assertEqual((self.root / 'engine-requests').read_text().splitlines(),
                         [self.status['requestId'], self.status['requestId']])
        self.assertEqual(self.folder_turn()['archiveSnapshotId'], 'a' * 64)
        self.assertGreaterEqual(self.folder_turn()['usefulSecondsUsed'], 2)
        self.assertFalse(self.folder_turn()['consumed'])

    def test_real_retry_exit_does_not_start_an_immediate_session(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('75 76')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)

    def test_normal_continuation_preserves_actual_global_lock_across_exec(self):
        self.completed_archive()
        (self.root / 'portable-export-lock.py').write_text((ROOT / 'scripts/portable-export-lock.py').read_text())
        self.environment.pop('PIROUTER_EXPORT_LOCKED')
        (self.root / 'verify-global-lock').touch()
        (self.root / 'engine-results').write_text('76 75')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 2)
        self.assertEqual((self.root / 'lock-checks').read_text().splitlines(), ['held', 'held'])

    def test_archive_start_between_timed_sessions_prevents_continuation_mount(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('76 75')
        (self.root / 'archive-after-engine').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)
        update = json.loads((self.root / 'updates').read_text().splitlines()[-1])
        self.assertEqual(update['message'], 'Pi is archiving; folder progress is saved')

    def test_offline_coordination_between_timed_sessions_stops_without_retry_loop(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('76 75')
        (self.root / 'offline-after-engine').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)
        self.assertTrue((self.support / 'archive-handoff.json').exists())

    def test_failed_release_after_timed_session_never_reexecutes(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('76 75')
        (self.root / 'busy-after-engine').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)
        self.assertTrue((self.support / 'portable-export-mount.json').exists())

    def test_time_machine_is_rechecked_before_next_timed_session(self):
        self.completed_archive()
        (self.root / 'engine-results').write_text('76 75')
        (self.root / 'next-time-machine-busy').touch()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)
        update = json.loads((self.root / 'updates').read_text().splitlines()[-1])
        self.assertEqual(update['message'], 'Waiting for Time Machine to finish; uploaded work is retained')

    def test_exhausted_folder_grant_yields_after_normal_timed_checkpoint(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        before = self.events().count('engine')
        grant = self.folder_turn()
        grant['remainingSeconds'] = 1
        (self.support / 'folder-export-turn.json').write_text(json.dumps(grant))
        (self.root / 'engine-results').write_text('75 76 75')
        (self.root / 'advance-progress').write_text('1024')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), before + 1)
        self.assertTrue(self.folder_turn()['consumed'])
        self.assertEqual(self.folder_turn()['remainingSeconds'], 0)
        self.assertIn('archive-trigger', self.events())

    def test_continuous_pinned_job_ignores_expired_exhausted_hour_window(self):
        self.continuous_job()
        self.assertEqual(self.run_wrapper().returncode, 0)
        filename = self.support / 'folder-export-turn.json'
        grant = self.folder_turn()
        original_archive = grant['archiveSnapshotId']
        grant.update(mode='bounded', consumed=True, remainingSeconds=0,
                     expiresAt=(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat())
        filename.write_text(json.dumps(grant))
        before = self.events().count('engine')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), before + 1)
        current = self.folder_turn()
        self.assertEqual(current['archiveSnapshotId'], original_archive)
        self.assertEqual(current['mode'], 'continuous')
        self.assertEqual(current['remainingSeconds'], 0)
        self.assertFalse(current['consumed'])
        arguments = json.loads((self.root / 'engine-args').read_text())
        self.assertEqual(arguments[arguments.index('--session-seconds') + 1], '600')

    def test_continuous_job_reexecutes_normal_sessions_until_success(self):
        self.continuous_job()
        (self.root / 'engine-results').write_text('76 76 0')
        (self.root / 'advance-progress').write_text('1024')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 3)
        self.assertEqual(self.events().count('helper-release'), 3)
        self.assertFalse((self.support / 'portable-export-job.json').exists())
        self.assertTrue(self.folder_turn()['consumed'])
        self.assertTrue((self.support / 'last-automatic-check').exists())

    def test_continuous_mode_still_hands_off_before_any_completed_archive(self):
        self.config.write_text(self.config.read_text().replace('CONTINUOUS_EXPORT=false', 'CONTINUOUS_EXPORT=true'))
        self.pin_job()
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('engine', self.events())
        self.assertNotIn('tmutil-mount', self.events())
        self.assertIn('archive-trigger', self.events())

    def test_continuous_mode_does_not_bypass_active_or_unknown_archive(self):
        self.continuous_job()
        self.assertEqual(self.run_wrapper().returncode, 0)
        before = self.events().count('engine')
        for active in (True, None):
            self.status['archiveCoordination']['serviceActive'] = active
            self.save_status()
            result = self.run_wrapper()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), before)

    def test_continuous_mode_real_error_closes_source_and_waits_for_next_launch(self):
        self.continuous_job()
        (self.root / 'engine-results').write_text('75 76 0')
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events().count('engine'), 1)
        self.assertEqual(self.events().count('helper-release'), 1)
        self.assertTrue((self.support / 'portable-export-job.json').exists())

    def test_live_or_unknown_archive_worker_blocks_an_existing_folder_window(self):
        self.completed_archive()
        self.assertEqual(self.run_wrapper().returncode, 0)
        previous = self.folder_turn()
        before = self.events().count('engine')
        for active in (True, None):
            self.status['archiveCoordination']['serviceActive'] = active
            self.save_status()
            self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual(self.events().count('engine'), before)
        self.assertEqual(self.folder_turn(), previous)

    def test_release_waits_for_transfer_child_and_leaves_snapshot_cwd(self):
        script = (ROOT / 'scripts/portable-export-macos.sh').read_text()
        functions = script[script.index('stop_children() {'):script.index('\ntrap cleanup EXIT')]
        helpers = script[script.index('api_archive_trigger() {'):script.index('\nlatest_snapshot() {')]
        receipt = self.support / 'portable-export-mount.json'
        receipt.write_text('{}')
        (self.root / 'attached').touch()
        (self.root / 'require-child-stopped').touch()
        child = self.root / 'child.sh'
        child.write_text('''#!/bin/bash
trap 'touch "$WRAPPER_TEST_ROOT/child-stopped"; exit 0' TERM
touch "$WRAPPER_TEST_ROOT/child-ready"
while true; do sleep 0.1; done
''')
        setup = '\n'.join(key + '=' + shlex.quote(value) for key, value in {
            'script_dir': str(self.root), 'mount_state_file': str(receipt),
            'job_file': str(self.support / 'portable-export-job.json'),
            'handoff_file': str(self.support / 'archive-handoff.json'),
            'response_file': str(self.root / 'response.json'),
            'tmutil_pid': '', 'request_id': '', 'session_mounted': 'true',
        }.items())
        harness = functions + '\n' + helpers + '\n' + setup + '''
api_get() { return 1; }
json_value() { return 1; }
cd "$WRAPPER_TEST_ROOT/2000-01-01-120000.backup"
/bin/bash "$WRAPPER_TEST_ROOT/child.sh" &
transfer_pid=$!
while [[ ! -e "$WRAPPER_TEST_ROOT/child-ready" ]]; do sleep 0.01; done
stop_children
release_for_archive
'''
        result = subprocess.run(['/bin/bash', '-u', '-c', harness], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'child-stopped').exists())
        self.assertFalse(receipt.exists())


if __name__ == '__main__':
    unittest.main()
