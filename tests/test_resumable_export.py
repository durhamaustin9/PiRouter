"""Exercise resumable exports in disposable snapshots with a durable receiver."""
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('resumable_export', ROOT / 'scripts/resumable-portable-export.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
SNAPSHOT = '2000-01-01-000000'
RECEIVER_SPEC = importlib.util.spec_from_file_location('receiver_test_harness', ROOT / 'tests/test_portable_receiver.py')
RECEIVER_TESTS = importlib.util.module_from_spec(RECEIVER_SPEC)
RECEIVER_SPEC.loader.exec_module(RECEIVER_TESTS)


class DurableReceiver:
    """Protocol double: acknowledged chunks and completed entries survive reconnects."""
    def __init__(self):
        self.entries = {}
        self.published = False
        self.chunks = []
        self.publish_requests = []
        self.fail_after_chunks = None
        self.lose_ack_once = False
        self.on_begin = None
        self.on_chunk = None
        self.publish_steps_remaining = 0
        self.lose_publish_ack_once = False
        self.batch = False
        self.batch_file_bytes = 65536
        self.batch_payload_bytes = 262144
        self.batch_requests = []
        self.lose_batch_ack_once = False
        self.fail_before_batch_once = False
        self.on_batch = None
        self.batch_reply = None

    def counters(self):
        files = [entry for entry in self.entries.values() if entry['kind'] == 'file']
        return dict(bytesCopied=sum(entry['offset'] for entry in files),
                    filesCopied=sum(entry['complete'] for entry in files))

    def connect(self):
        backend = self

        class Connection:
            def __init__(self):
                self.pending = None
                self.sent = 0
                self.interrupted = False

            def start(self):
                ready = dict(ok=True, published=backend.published, **backend.counters())
                if backend.batch:
                    ready['batch'] = dict(version=1, maxEntries=64, maxFileBytes=backend.batch_file_bytes,
                                          maxPayloadBytes=backend.batch_payload_bytes)
                return ready

            def request(self, message):
                if self.interrupted or (backend.fail_after_chunks is not None and self.sent >= backend.fail_after_chunks):
                    raise EXPORT.PauseExport('Simulated network disconnect')
                op = message['op']
                path = message.get('path')
                if op == 'batch':
                    if not backend.batch:
                        raise AssertionError('Legacy receiver cannot batch')
                    backend.batch_requests.append(message)
                    if backend.fail_before_batch_once:
                        backend.fail_before_batch_once = False
                        raise EXPORT.PauseExport('Disconnected before batch commit')
                    prepared = {}
                    for item in message['entries']:
                        data = base64.b64decode(item['data'], validate=True) if item['kind'] == 'file' else b''
                        if item['kind'] == 'file':
                            assert len(data) == item['size'] <= backend.batch_file_bytes
                            assert hashlib.sha256(data).hexdigest() == item['sha256']
                        prepared[item['path']] = dict(item, offset=len(data), complete=True,
                                                       data=bytearray(data))
                    assert len(prepared) == len(message['entries']) <= 64
                    assert sum(len(item['data']) for item in prepared.values()) <= backend.batch_payload_bytes
                    backend.entries.update(prepared)
                    self.sent += sum(item['kind'] == 'file' and item['size'] > 0 for item in message['entries'])
                    if backend.on_batch:
                        backend.on_batch(message)
                    if backend.lose_batch_ack_once:
                        backend.lose_batch_ack_once = False
                        raise EXPORT.PauseExport('Disconnected after durable batch commit')
                    reply = dict(ok=True, batchComplete=True,
                                 paths=[item['path'] for item in message['entries']], **backend.counters())
                    return backend.batch_reply(reply) if backend.batch_reply else reply
                if op == 'begin':
                    old = backend.entries.get(path)
                    if old is None or old['identity'] != message['identity']:
                        backend.entries[path] = dict(message, offset=0,
                                                     complete=message['kind'] != 'file', data=bytearray())
                    entry = backend.entries[path]
                    if backend.on_begin:
                        backend.on_begin(message)
                    return dict(ok=True, complete=entry['complete'], offset=entry['offset'])
                if op == 'chunk':
                    entry = backend.entries[path]
                    if message['offset'] != entry['offset']:
                        raise EXPORT.ExportError('Unexpected fake receiver offset')
                    self.pending = message
                    return dict(ok=True, needData=True, offset=entry['offset'])
                if op == 'finish':
                    entry = backend.entries[path]
                    if entry['offset'] != entry['size']:
                        raise EXPORT.ExportError('Cannot finish a short file')
                    entry['complete'] = True
                    return dict(ok=True, complete=True, offset=entry['offset'])
                if op == 'status':
                    return dict(ok=True, **backend.counters())
                if op == 'publish':
                    backend.publish_requests.append(message)
                    files = [entry for entry in backend.entries.values() if entry['kind'] == 'file']
                    if len(backend.entries) != message['expectedEntries'] or len(files) != message['expectedFiles']:
                        raise EXPORT.ExportError('Incomplete manifest')
                    if any(not entry['complete'] for entry in backend.entries.values()):
                        raise EXPORT.ExportError('Unfinished entries')
                    for volume in message['volumes']:
                        count = sum(entry['path'].startswith(volume['name'] + '/') for entry in files)
                        if count != volume['regularFiles'] or count == 0:
                            raise EXPORT.ExportError('Missing volume files')
                    if backend.publish_steps_remaining:
                        backend.publish_steps_remaining -= 1
                        if backend.lose_publish_ack_once:
                            backend.lose_publish_ack_once = False
                            raise EXPORT.PauseExport('Disconnected after a bounded verification checkpoint')
                        return dict(ok=True, published=False, finalizing=True,
                                    checkedEntries=len(backend.entries) - backend.publish_steps_remaining,
                                    totalEntries=len(backend.entries), **backend.counters())
                    backend.published = True
                    return dict(ok=True, published=True, **backend.counters())
                raise AssertionError(message)

            def write_chunk(self, data):
                message = self.pending
                assert len(data) == message['length']
                assert hashlib.sha256(data).hexdigest() == message['sha256']
                entry = backend.entries[message['path']]
                entry['data'].extend(data)
                entry['offset'] += len(data)
                backend.chunks.append((message['path'], message['offset'], len(data)))
                self.sent += 1
                if backend.on_chunk:
                    backend.on_chunk(message)
                if backend.lose_ack_once:
                    backend.lose_ack_once = False
                    raise EXPORT.PauseExport('Connection dropped after durable commit, before acknowledgement')
                return dict(ok=True, offset=entry['offset'])

            def interrupt(self):
                self.interrupted = True

            def close(self):
                pass

        return Connection()


class TrackedFile:
    def __init__(self, handle, reads):
        self.handle = handle
        self.reads = reads

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, size):
        self.reads.append((self.handle.tell(), size))
        return self.handle.read(size)


class ResumableExport(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ, {'PIROUTER_URLS': '', 'PIROUTER_TOKEN': '', 'PIROUTER_REQUEST_ID': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source' / (SNAPSHOT + '.backup')
        self.main = self.source / 'Macintosh HD - Data'
        self.main.mkdir(parents=True)
        self.receiver = DurableReceiver()
        self.args = Namespace(source=str(self.source), snapshot=SNAPSHOT,
                              state_dir=str(self.root / 'state'), host='unused',
                              root='/mnt/user/test', ssh_key='unused', receiver_script='unused',
                              bandwidth_kb=1024 * 1024, session_seconds=0, max_chunks=None)
        self.chunk_patch = mock.patch.object(EXPORT, 'CHUNK_SIZE', 128 * 1024)
        self.chunk_patch.start()
        self.addCleanup(self.chunk_patch.stop)

    def run_export(self, **overrides):
        args = Namespace(**dict(vars(self.args), **overrides))
        engine = EXPORT.ExportEngine(args, transport_factory=self.receiver.connect)
        with redirect_stdout(io.StringIO()):
            result = engine.run()
        return result

    def test_large_file_advances_across_repeated_disconnects_without_rereading_prefix(self):
        content = os.urandom(EXPORT.CHUNK_SIZE * 3 + 47)
        (self.main / 'large-vm.hds').write_bytes(content)
        self.receiver.fail_after_chunks = 1
        reads = []
        original_fdopen = os.fdopen
        with mock.patch.object(EXPORT.os, 'fdopen', side_effect=lambda fd, mode: TrackedFile(original_fdopen(fd, mode), reads)):
            for expected in range(1, 5):
                acknowledged_before = self.receiver.counters()['bytesCopied']
                first_read = len(reads)
                self.assertEqual(self.run_export(), 75)
                self.assertTrue(all(offset >= acknowledged_before for offset, length in reads[first_read:]))
                saved = self.receiver.entries['Macintosh HD - Data/large-vm.hds']['offset']
                self.assertEqual(saved, min(expected * EXPORT.CHUNK_SIZE, len(content)))
                self.assertFalse(self.receiver.published)
            self.receiver.fail_after_chunks = None
            self.assertEqual(self.run_export(), 0)
        self.assertEqual([offset for path, offset, length in self.receiver.chunks],
                         [0, EXPORT.CHUNK_SIZE, EXPORT.CHUNK_SIZE * 2, EXPORT.CHUNK_SIZE * 3])
        copied = self.receiver.entries['Macintosh HD - Data/large-vm.hds']['data']
        self.assertEqual(hashlib.sha256(copied).digest(), hashlib.sha256(content).digest())
        self.assertTrue(self.receiver.published)

    def test_lost_acknowledgement_uses_receiver_offset_on_reconnect(self):
        content = os.urandom(EXPORT.CHUNK_SIZE * 2 + 1)
        (self.main / 'large').write_bytes(content)
        self.receiver.lose_ack_once = True
        self.assertEqual(self.run_export(), 75)
        self.assertFalse(self.receiver.published)
        self.assertEqual(self.run_export(), 0)
        offsets = [offset for path, offset, length in self.receiver.chunks]
        self.assertEqual(offsets, [0, EXPORT.CHUNK_SIZE, EXPORT.CHUNK_SIZE * 2])
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/large']['data'], content)

    def test_completed_source_files_are_not_read_again(self):
        completed = self.main / 'a-small'
        completed.write_bytes(b'first completed file')
        (self.main / 'b-large').write_bytes(os.urandom(EXPORT.CHUNK_SIZE * 2))
        self.assertEqual(self.run_export(max_chunks=1), 75)
        self.assertTrue(self.receiver.entries['Macintosh HD - Data/a-small']['complete'])
        original_open = os.open

        def guarded_open(filename, *args, **kwargs):
            if str(filename) == str(completed):
                raise AssertionError('Completed source file was reopened')
            return original_open(filename, *args, **kwargs)

        with mock.patch.object(EXPORT.os, 'open', side_effect=guarded_open):
            self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.counters()['filesCopied'], 2)

    def test_uploads_begin_before_remaining_directories_are_scanned(self):
        (self.main / 'a-first').write_bytes(b'upload immediately')
        nested = self.main / 'z-later' / 'nested'
        nested.mkdir(parents=True)
        (nested / 'last').write_bytes(b'later file')
        pending_during_upload = []

        def inspect_queue(message):
            database = self.root / 'state' / (SNAPSHOT + '.sqlite3')
            with sqlite3.connect(str(database)) as db:
                pending_during_upload.append(db.execute('SELECT count(*) FROM directories WHERE scanned=0').fetchone()[0])

        self.receiver.on_chunk = inspect_queue
        self.assertEqual(self.run_export(), 0)
        self.assertGreater(pending_during_upload[0], 0)

    def test_empty_or_missing_volumes_never_publish(self):
        (self.main / 'main-file').write_bytes(b'actual user data')
        other = self.source / 'Other'
        other.mkdir()
        self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.publish_requests, [])
        (other / 'external-file').write_bytes(b'other volume data')
        self.assertEqual(self.run_export(max_chunks=1), 75)
        shutil.rmtree(other)
        self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.publish_requests, [])

    def test_root_controls_and_protected_state_are_omitted_but_nested_user_metadata_survives(self):
        (self.source / '.DS_Store').write_bytes(b'not a volume')
        (self.source / '.exclusions.plist').write_bytes(b'Time Machine control state')
        (self.source / '.pirouter-internal').write_bytes(b'reserved')
        (self.main / '.pirouter-user-file').write_bytes(b'legitimate user data')
        protected = self.main / 'private' / 'var'
        protected.mkdir(parents=True)
        (protected / 'secret').write_bytes(b'excluded protected operating system state')
        (self.main / 'ordinary').write_bytes(b'ordinary')
        self.assertEqual(self.run_export(), 0)
        self.assertIn('Macintosh HD - Data/.pirouter-user-file', self.receiver.entries)
        self.assertNotIn('Macintosh HD - Data/private/var', self.receiver.entries)
        self.assertFalse(any(path.startswith('.') for path in self.receiver.entries))

    def test_symlinks_are_preserved_without_reading_their_targets(self):
        (self.main / 'file').write_bytes(b'content')
        os.symlink('/a/target/outside/the/backup', self.main / 'link')
        self.assertEqual(self.run_export(), 0)
        link = self.receiver.entries['Macintosh HD - Data/link']
        self.assertEqual(link['kind'], 'symlink')
        self.assertEqual(link['link'], '/a/target/outside/the/backup')

    def test_pinned_snapshot_cannot_switch_to_a_different_source(self):
        (self.main / 'large').write_bytes(os.urandom(EXPORT.CHUNK_SIZE * 2))
        self.assertEqual(self.run_export(max_chunks=1), 75)
        new_source = self.root / 'new-source'
        (new_source / 'Macintosh HD - Data').mkdir(parents=True)
        (new_source / 'Macintosh HD - Data' / 'large').write_bytes(b'different backup')
        before = list(self.receiver.chunks)
        self.assertEqual(self.run_export(source=str(new_source)), 75)
        self.assertEqual(self.receiver.chunks, before)
        self.assertFalse(self.receiver.published)
        self.assertEqual(self.run_export(), 0)

    def test_a_newer_mounted_backup_cannot_be_labeled_as_the_pinned_snapshot(self):
        newer = self.root / '2000-01-03-120000.backup'
        (newer / 'Macintosh HD - Data').mkdir(parents=True)
        (newer / 'Macintosh HD - Data' / 'file').write_bytes(b'newer snapshot')
        self.assertEqual(self.run_export(source=str(newer)), 75)
        self.assertEqual(self.receiver.entries, {})
        self.assertFalse(self.receiver.published)

    def test_active_job_lock_is_not_stolen_and_does_not_report_a_pause(self):
        (self.main / 'file').write_bytes(b'backup')
        state = self.root / 'state'
        state.mkdir()
        with (state / (SNAPSHOT + '.lock')).open('a+') as lock:
            EXPORT.fcntl.flock(lock, EXPORT.fcntl.LOCK_EX | EXPORT.fcntl.LOCK_NB)
            with mock.patch.object(EXPORT.Progress, 'report') as report:
                self.assertEqual(self.run_export(), 0)
            report.assert_not_called()
        self.assertEqual(self.run_export(), 0)

    def test_changes_between_scan_and_read_are_rejected(self):
        target = self.main / 'file'
        target.write_bytes(b'original')

        def change_source(message):
            if message['path'] == 'Macintosh HD - Data/file':
                target.write_bytes(b'changed data')

        self.receiver.on_begin = change_source
        self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.chunks, [])
        self.assertEqual(self.receiver.publish_requests, [])

    def test_a_session_budget_keeps_partial_work_unpublished(self):
        (self.main / 'file').write_bytes(os.urandom(EXPORT.CHUNK_SIZE * 2))
        self.assertEqual(self.run_export(max_chunks=1), 75)
        self.assertEqual(self.receiver.publish_requests, [])
        with sqlite3.connect(str(self.root / 'state' / (SNAPSHOT + '.sqlite3'))) as db:
            offset, done = db.execute("SELECT offset,done FROM entries WHERE kind='file'").fetchone()
        self.assertEqual(offset, EXPORT.CHUNK_SIZE)
        self.assertEqual(done, 0)
        self.assertEqual(self.run_export(), 0)

    def test_timed_session_boundary_is_normal_and_retains_acknowledged_progress(self):
        content = os.urandom(EXPORT.CHUNK_SIZE * 2 + 41)
        (self.main / 'file').write_bytes(content)
        args = Namespace(**dict(vars(self.args), session_seconds=600))
        engine = EXPORT.ExportEngine(args, transport_factory=self.receiver.connect)
        self.receiver.on_chunk = lambda message: setattr(engine, 'deadline', time.monotonic() - 1)
        output = io.StringIO()
        with mock.patch.object(EXPORT.Progress, 'report') as report, redirect_stdout(output):
            self.assertEqual(engine.run(), 76)
        report.assert_called_with('paused', pause_reason='session-budget')
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual((result['state'], result['pauseReason']), ('paused', 'session-budget'))
        self.assertEqual(result['bytesCopied'], EXPORT.CHUNK_SIZE)
        self.assertEqual(self.receiver.publish_requests, [])
        with sqlite3.connect(str(self.root / 'state' / (SNAPSHOT + '.sqlite3'))) as db:
            offset, done = db.execute("SELECT offset,done FROM entries WHERE kind='file'").fetchone()
        self.assertEqual((offset, done), (EXPORT.CHUNK_SIZE, 0))
        self.receiver.on_chunk = None
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/file']['data'], content)

    def test_scheduled_api_report_has_continuation_reason_and_no_error_detail(self):
        progress = EXPORT.Progress(SNAPSHOT)
        progress.urls = ['https://example.invalid']
        progress.token = 'test-only-token'
        progress.request_id = 'test-only-request'
        progress.set(bytes_copied=1234, files_copied=12)
        with mock.patch.object(EXPORT.urllib.request, 'urlopen') as send:
            progress.report('paused', 'A scheduled boundary is not a transfer error', pause_reason='session-budget')
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload['state'], 'paused')
        self.assertEqual(payload['pauseReason'], 'session-budget')
        self.assertEqual(payload['message'], 'Continuing automatically; saved work is retained')
        self.assertNotIn('errorDetail', payload)
        self.assertEqual((payload['bytesCopied'], payload['filesCopied']), (1234, 12))

    def test_real_interruption_and_network_report_remain_retryable_errors(self):
        engine = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        engine.stop_requested = True
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(engine.run(), 75)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertNotIn('pauseReason', result)
        self.assertIn('interrupted', result['detail'])
        progress = EXPORT.Progress(SNAPSHOT)
        progress.urls = ['https://example.invalid']
        progress.token = 'test-only-token'
        progress.request_id = 'test-only-request'
        with mock.patch.object(EXPORT.urllib.request, 'urlopen') as send:
            progress.report('paused', 'The network disconnected')
        payload = json.loads(send.call_args.args[0].data)
        self.assertNotIn('pauseReason', payload)
        self.assertEqual(payload['errorDetail'], 'The network disconnected')

    def test_pacing_never_sleeps_for_negative_time_when_clock_passes_target(self):
        engine = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        engine.next_payload_time = 1
        slept = []

        def sleep(seconds):
            if seconds < 0:
                raise ValueError('sleep length must be non-negative')
            slept.append(seconds)

        # The clock passes the deadline between successive samples. Previously
        # the while-condition sampled once and sleep sampled again, going negative.
        with mock.patch.object(EXPORT.time, 'monotonic', side_effect=[1.0, 1.0, 2.0]), \
                mock.patch.object(EXPORT.time, 'sleep', side_effect=sleep):
            engine._pace(1)
        self.assertEqual(len(slept), 1)
        self.assertGreaterEqual(slept[0], 0)

    def test_pacing_counts_transfer_and_verification_time_toward_the_limit(self):
        engine = EXPORT.ExportEngine(Namespace(**dict(vars(self.args), bandwidth_kb=1)),
                                     transport_factory=self.receiver.connect)
        engine.next_payload_time = 0
        clock = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        with mock.patch.object(EXPORT.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(EXPORT.time, 'sleep', side_effect=sleep):
            engine._pace(1024)
            self.assertAlmostEqual(sum(slept), 1.0)
            clock[0] += 2.0  # The preceding transfer and durable receiver ACK.
            slept.clear()
            engine._pace(1024)
            self.assertEqual(slept, [])
            engine._pace(1024)
            self.assertAlmostEqual(sum(slept), 1.0)

    def test_pacing_does_not_accumulate_burst_credit_during_long_idle_periods(self):
        engine = EXPORT.ExportEngine(Namespace(**dict(vars(self.args), bandwidth_kb=1)),
                                     transport_factory=self.receiver.connect)
        engine.next_payload_time = 0
        clock = [100.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        with mock.patch.object(EXPORT.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(EXPORT.time, 'sleep', side_effect=sleep):
            engine._pace(1024)
            self.assertEqual(slept, [])
            engine._pace(1024)
            self.assertAlmostEqual(sum(slept), 1.0)

    def test_unexpected_failure_reports_paused_and_retains_durable_work_for_retry(self):
        content = os.urandom(EXPORT.CHUNK_SIZE * 2 + 1)
        (self.main / 'large').write_bytes(content)

        def fail_after_receiver_commit(message):
            raise ValueError('unexpected test failure after durable chunk write')

        self.receiver.on_chunk = fail_after_receiver_commit
        with mock.patch.object(EXPORT.Progress, 'report') as report, redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_export(), 75)
        self.assertEqual(report.call_args.args[0], 'paused')
        self.assertIn('ValueError', report.call_args.args[1])
        self.assertEqual(self.receiver.counters()['bytesCopied'], EXPORT.CHUNK_SIZE)
        self.assertFalse(self.receiver.published)
        self.receiver.on_chunk = None
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/large']['data'], content)

    def test_flat_directory_scan_commits_batches_before_upload_and_resumes(self):
        for index in range(EXPORT.SCAN_BATCH_SIZE + 15):
            (self.main / ('file-%04d' % index)).write_bytes(b'small user file')
        self.assertEqual(self.run_export(max_chunks=1), 75)
        database = self.root / 'state' / (SNAPSHOT + '.sqlite3')
        with sqlite3.connect(str(database)) as db:
            scanned = db.execute('SELECT scanned FROM directories WHERE path=?', ('Macintosh HD - Data',)).fetchone()[0]
            count = db.execute("SELECT count(*) FROM entries WHERE kind='file'").fetchone()[0]
        self.assertEqual(scanned, 0)
        self.assertEqual(count, EXPORT.SCAN_BATCH_SIZE)
        self.assertEqual(self.receiver.counters()['filesCopied'], 1)
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.counters()['filesCopied'], EXPORT.SCAN_BATCH_SIZE + 15)

    def test_final_verification_reconnects_and_never_reports_early_publication(self):
        (self.main / 'file').write_bytes(b'already uploaded data')
        self.receiver.publish_steps_remaining = 3
        self.receiver.lose_publish_ack_once = True
        self.assertEqual(self.run_export(), 75)
        self.assertFalse(self.receiver.published)
        self.assertEqual(self.receiver.counters()['filesCopied'], 1)
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(len(self.receiver.publish_requests), 4)
        self.assertTrue(self.receiver.published)

    def test_personal_folders_and_cloud_data_for_every_user_precede_regenerable_caches(self):
        for username in ('alice', 'zoe'):
            base = 'Macintosh HD - Data/Users/' + username + '/'
            caches = [base + suffix for suffix in (
                '.npm/_cacache/item', '.cache/tool/item', '.local/cache/item',
                'Library/Caches/item', 'Library/Containers/app/Data/Library/Caches/item')]
            personal = [base + suffix for suffix in (
                'Documents/project', 'Desktop/note', 'Pictures/photo', 'Movies/video',
                'Music/song', 'Downloads/download', 'Library/Mobile Documents/file',
                'Library/MobileDocuments/file', 'Library/CloudStorage/file')]
            for path in personal:
                self.assertTrue(all(EXPORT.priority(path) < EXPORT.priority(cache) for cache in caches), path)
                self.assertLess(EXPORT.priority(base.rstrip('/')), EXPORT.priority(path))
            self.assertLess(EXPORT.priority(base + 'Library'), EXPORT.priority(base + 'Library/CloudStorage/file'))
            self.assertLess(EXPORT.priority(base + '.ssh/config'), EXPORT.priority(base + '.cache/item'))
            self.assertLess(EXPORT.priority(base + '.local/share/user-data'), EXPORT.priority(base + '.local/cache/item'))

    def test_personal_uploads_finish_before_cache_trees_without_excluding_any_data(self):
        personal = []
        caches = []
        for username in ('alice', 'zoe'):
            home = self.main / 'Users' / username
            for suffix in ('.npm/_cacache/a/file', '.cache/tool/file', '.local/cache/tool/file', 'Library/Caches/tool/file'):
                path = home / suffix
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'regenerable cache retained')
                caches.append(str(path.relative_to(self.source)))
            for suffix in ('Documents/nested/file', 'Desktop/file', 'Pictures/file', 'Music/file', 'Movies/file',
                           'Downloads/file', 'Library/Mobile Documents/app/file', 'Library/CloudStorage/provider/file'):
                path = home / suffix
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'important personal data')
                personal.append(str(path.relative_to(self.source)))
        self.assertEqual(self.run_export(), 0)
        order = [path for path, offset, length in self.receiver.chunks]
        self.assertLess(max(order.index(path) for path in personal), min(order.index(path) for path in caches))
        self.assertEqual(self.receiver.counters()['filesCopied'], len(personal) + len(caches))
        self.assertTrue(self.receiver.published)

    def test_upgrade_reprioritizes_old_queued_work_without_resetting_saved_progress(self):
        documents = self.main / 'Users' / 'alice' / 'Documents'
        cache = self.main / 'Users' / 'alice' / '.npm'
        documents.mkdir(parents=True)
        cache.mkdir()
        (documents / 'file').write_bytes(b'personal')
        (cache / 'file').write_bytes(b'cached')
        engine = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        engine._open_state()
        try:
            with engine.db:
                for path, done, offset in ((documents / 'file', 1, 8), (cache / 'file', 0, 3)):
                    record = EXPORT.metadata(str(path.relative_to(self.source)), path.lstat())
                    engine.db.execute('''INSERT INTO entries(path,kind,identity,size,mode,mtime_ns,link,priority,done,offset)
                        VALUES(:path,:kind,:identity,:size,:mode,:mtime_ns,:link,0,:done,:offset)''',
                        dict(record, done=done, offset=offset))
                for path in (documents, cache):
                    engine.db.execute('INSERT INTO directories(path,priority,scanned) VALUES(?,0,0)',
                                      (str(path.relative_to(self.source)),))
                engine._set_meta('bytes_copied', 11)
                engine._set_meta('files_copied', 1)
                engine.db.execute("DELETE FROM job WHERE key='priority_version'")
        finally:
            engine.db.close()
            engine.lock_handle.close()
        reopened = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        reopened._open_state()
        try:
            rows = reopened.db.execute('SELECT path,done,offset,priority FROM entries ORDER BY priority,path').fetchall()
            self.assertTrue(rows[0]['path'].endswith('/Documents/file'))
            self.assertEqual((rows[0]['done'], rows[0]['offset']), (1, 8))
            self.assertEqual((rows[1]['done'], rows[1]['offset']), (0, 3))
            queue = reopened.db.execute('SELECT path FROM directories WHERE scanned=0 ORDER BY priority,path').fetchall()
            self.assertTrue(queue[0]['path'].endswith('/Documents'))
            self.assertEqual(reopened._meta('bytes_copied'), '11')
            self.assertEqual(reopened._meta('files_copied'), '1')
            self.assertEqual(reopened._meta('priority_version'), EXPORT.PRIORITY_VERSION)
        finally:
            reopened.db.close()
            reopened.lock_handle.close()

    def test_queued_cache_file_waits_for_personal_descendants_still_being_discovered(self):
        home = self.main / 'Users' / 'alice'
        (home / '.npm').mkdir(parents=True)
        (home / '.npm' / 'cached').write_bytes(b'cache retained')
        (home / 'Documents' / 'nested').mkdir(parents=True)
        (home / 'Documents' / 'nested' / 'important').write_bytes(b'personal first')
        seed = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        seed._open_state()
        seed._check_volumes()
        try:
            for path in ('', 'Macintosh HD - Data', 'Macintosh HD - Data/Users',
                         'Macintosh HD - Data/Users/alice', 'Macintosh HD - Data/Users/alice/.npm'):
                seed._scan_directory(path)
            with seed.db:
                seed.db.execute('UPDATE entries SET priority=0')
                seed.db.execute('UPDATE directories SET priority=0 WHERE path<>\'\'')
                seed.db.execute("DELETE FROM job WHERE key='priority_version'")
        finally:
            seed.db.close()
            seed.lock_handle.close()
        self.assertEqual(self.run_export(), 0)
        self.assertEqual([path for path, offset, length in self.receiver.chunks], [
            'Macintosh HD - Data/Users/alice/Documents/nested/important',
            'Macintosh HD - Data/Users/alice/.npm/cached'])

    def test_queued_large_other_user_file_cannot_consume_the_personal_folder_turn(self):
        home = self.main / 'Users' / 'alice'
        (home / 'Documents' / 'nested').mkdir(parents=True)
        (home / 'Documents' / 'nested' / 'important').write_bytes(b'personal first')
        (home / 'a-large-vm.hds').write_bytes(os.urandom(EXPORT.CHUNK_SIZE * 2 + 1))
        seed = EXPORT.ExportEngine(self.args, transport_factory=self.receiver.connect)
        seed._open_state()
        seed._check_volumes()
        try:
            for path in ('', 'Macintosh HD - Data', 'Macintosh HD - Data/Users', 'Macintosh HD - Data/Users/alice'):
                seed._scan_directory(path)
        finally:
            seed.db.close()
            seed.lock_handle.close()
        self.assertEqual(self.run_export(max_chunks=1), 75)
        self.assertEqual([path for path, offset, length in self.receiver.chunks], [
            'Macintosh HD - Data/Users/alice/Documents/nested/important'])
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.counters()['filesCopied'], 2)


class BatchExport(unittest.TestCase):
    setUp = ResumableExport.setUp
    run_export = ResumableExport.run_export

    def database(self):
        return sqlite3.connect(str(self.root / 'state' / (SNAPSHOT + '.sqlite3')))

    def test_medium_documents_use_one_request_with_new_bounds(self):
        self.receiver.batch = True
        self.receiver.batch_file_bytes = EXPORT.BATCH_MAX_FILE_BYTES
        self.receiver.batch_payload_bytes = EXPORT.BATCH_MAX_PAYLOAD_BYTES
        contents = [os.urandom(300 * 1024), os.urandom(EXPORT.BATCH_MAX_FILE_BYTES)]
        for index, data in enumerate(contents):
            (self.main / ('document-%d' % index)).write_bytes(data)
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.chunks, [])
        batches = [request for request in self.receiver.batch_requests
                   if any(entry['kind'] == 'file' for entry in request['entries'])]
        self.assertEqual(len(batches), 2)
        self.assertTrue(all(len(json.dumps(request, ensure_ascii=True).encode()) <= EXPORT.BATCH_MAX_JSON_BYTES
                            for request in batches))
        self.assertEqual(self.receiver.counters(), dict(bytesCopied=sum(map(len, contents)), filesCopied=2))

    def test_medium_documents_honor_older_receivers_smaller_bounds(self):
        self.receiver.batch = True  # The double advertises the original 64 KiB bound.
        data = os.urandom(300 * 1024)
        (self.main / 'document').write_bytes(data)
        self.assertEqual(self.run_export(), 0)
        self.assertTrue(self.receiver.chunks)
        self.assertFalse(any(entry['kind'] == 'file' for request in self.receiver.batch_requests
                             for entry in request['entries']))
        self.assertEqual(self.receiver.counters(), dict(bytesCopied=len(data), filesCopied=1))

    def test_many_small_files_use_durable_bounded_batches(self):
        self.receiver.batch = True
        contents = {}
        for index in range(150):
            content = ('user file %d' % index).encode()
            path = self.main / ('file-%04d' % index)
            path.write_bytes(content)
            contents[str(path.relative_to(self.source))] = content
        os.symlink('file-0000', self.main / 'link')
        self.assertEqual(self.run_export(), 0)
        self.assertLess(len(self.receiver.batch_requests), 8)
        self.assertEqual(self.receiver.chunks, [])
        for request in self.receiver.batch_requests:
            self.assertLessEqual(len(request['entries']), 64)
            self.assertLessEqual(len(EXPORT.json.dumps(request, ensure_ascii=True).encode()), 900 * 1024)
            self.assertTrue(all(isinstance(item['mtime'], int) for item in request['entries']))
        for path, content in contents.items():
            self.assertEqual(self.receiver.entries[path]['data'], content)
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/link']['link'], 'file-0000')
        with self.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM entries WHERE done=0').fetchone()[0], 0)
            self.assertEqual(int(db.execute("SELECT value FROM job WHERE key='bytes_copied'").fetchone()[0]),
                             sum(map(len, contents.values())))

    def test_lookahead_fills_batches_across_small_directories(self):
        self.receiver.batch = True
        for index in range(24):
            directory = self.main / ('folder-%03d' % index)
            directory.mkdir()
            (directory / 'note').write_bytes(b'personal document')
        self.assertEqual(self.run_export(), 0)
        file_batches = [[item for item in request['entries'] if item['kind'] == 'file']
                        for request in self.receiver.batch_requests]
        self.assertGreaterEqual(max(map(len, file_batches)), 12)
        self.assertTrue(any(len({str(Path(item['path']).parent) for item in batch}) >= 12
                            for batch in file_batches))

    def test_payload_and_encoded_frame_limits_split_without_skipping_files(self):
        self.receiver.batch = True
        for index in range(9):
            (self.main / ('small-%02d' % index)).write_bytes(os.urandom(65536))
        with mock.patch.object(EXPORT, 'BATCH_MAX_JSON_BYTES', 100 * 1024):
            self.assertEqual(self.run_export(), 0)
        for request in self.receiver.batch_requests:
            self.assertLessEqual(len(EXPORT.json.dumps(request, ensure_ascii=True).encode()), 100 * 1024)
            self.assertLessEqual(sum(item['size'] for item in request['entries'] if item['kind'] == 'file'), 262144)
        self.assertEqual(self.receiver.counters(), dict(bytesCopied=9 * 65536, filesCopied=9))

    def test_lost_durable_batch_ack_resumes_same_database_without_double_counting(self):
        (self.main / 'a-already-done').write_bytes(b'already done')
        (self.main / 'b-other').write_bytes(b'new data')
        self.assertEqual(self.run_export(max_chunks=1), 75)
        self.receiver.batch = True
        self.receiver.lose_batch_ack_once = True
        self.assertEqual(self.run_export(), 75)
        durable = self.receiver.counters()
        self.assertEqual(durable['filesCopied'], 2)
        with self.database() as db:
            self.assertEqual(db.execute("SELECT done FROM entries WHERE path LIKE '%/b-other'").fetchone()[0], 0)
            identity = db.execute("SELECT value FROM job WHERE key='source_id'").fetchone()[0]
        original_open = os.open
        def guarded_open(filename, *args, **kwargs):
            if str(filename).endswith('/a-already-done'):
                raise AssertionError('Previously completed source was reread')
            return original_open(filename, *args, **kwargs)
        with mock.patch.object(EXPORT.os, 'open', side_effect=guarded_open):
            self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.counters(), durable)
        with self.database() as db:
            self.assertEqual(db.execute("SELECT value FROM job WHERE key='source_id'").fetchone()[0], identity)
            self.assertEqual(int(db.execute("SELECT value FROM job WHERE key='files_copied'").fetchone()[0]), 2)

    def test_disconnect_before_batch_commit_never_checkpoints_or_publishes(self):
        self.receiver.batch = True
        self.receiver.fail_before_batch_once = True
        (self.main / 'file').write_bytes(b'readable file')
        self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.entries, {})
        with self.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM entries WHERE done=1').fetchone()[0], 0)
        self.assertEqual(self.receiver.publish_requests, [])
        self.assertEqual(self.run_export(), 0)

    def test_failed_local_batch_commit_rolls_back_done_rows_and_counters(self):
        self.receiver.batch = True
        (self.main / 'file').write_bytes(b'committed remotely')
        original = EXPORT.ExportEngine._checkpoint_batch
        def failing_checkpoint(engine, entries, counters):
            set_meta = engine._set_meta
            def fail_second_counter(key, value):
                if key == 'files_copied':
                    raise OSError('local checkpoint interrupted')
                set_meta(key, value)
            with mock.patch.object(engine, '_set_meta', side_effect=fail_second_counter):
                original(engine, entries, counters)
        with mock.patch.object(EXPORT.ExportEngine, '_checkpoint_batch', side_effect=failing_checkpoint, autospec=True):
            self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.counters()['filesCopied'], 1)
        with self.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM entries WHERE done=1').fetchone()[0], 0)
            self.assertEqual(int(db.execute("SELECT value FROM job WHERE key='bytes_copied'").fetchone()[0]), 0)
            self.assertEqual(int(db.execute("SELECT value FROM job WHERE key='files_copied'").fetchone()[0]), 0)
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.counters()['filesCopied'], 1)

    def test_invalid_batch_ack_never_marks_local_entries_complete(self):
        self.receiver.batch = True
        (self.main / 'file').write_bytes(b'content')
        for change in (lambda r: dict(r, paths=[]), lambda r: dict(r, bytesCopied=True),
                       lambda r: dict(r, filesCopied=-1), lambda r: dict(r, batchComplete=False)):
            self.receiver.batch_reply = change
            self.assertEqual(self.run_export(), 75)
            with self.database() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM entries WHERE done=1').fetchone()[0], 0)
        self.receiver.batch_reply = None
        self.assertEqual(self.run_export(), 0)

    def test_source_change_after_read_is_rejected_before_any_batch_is_sent(self):
        self.receiver.batch = True
        target = self.main / 'file'
        target.write_bytes(b'original file')
        with mock.patch.object(EXPORT.ExportEngine, '_pace', side_effect=lambda length: target.write_bytes(b'changed')):
            self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.batch_requests, [])
        self.assertEqual(self.receiver.publish_requests, [])

    def test_small_file_with_existing_chunk_offset_retains_legacy_resume(self):
        content = os.urandom(4096)
        (self.main / 'a-partial').write_bytes(content)
        with mock.patch.object(EXPORT, 'CHUNK_SIZE', 1024):
            self.assertEqual(self.run_export(max_chunks=1), 75)
            self.receiver.batch = True
            self.assertEqual(self.run_export(), 0)
        self.assertEqual([offset for path, offset, length in self.receiver.chunks], [0, 1024, 2048, 3072])
        self.assertFalse(any(item['path'].endswith('/a-partial') for request in self.receiver.batch_requests
                             for item in request['entries']))
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/a-partial']['data'], content)

    def test_chunk_fixture_budget_bounds_batch_files_and_flat_scan_still_streams(self):
        self.receiver.batch = True
        for index in range(EXPORT.SCAN_BATCH_SIZE + 15):
            (self.main / ('file-%04d' % index)).write_bytes(b'small')
        self.assertEqual(self.run_export(max_chunks=1), 75)
        self.assertEqual(self.receiver.counters()['filesCopied'], 1)
        with self.database() as db:
            self.assertEqual(db.execute("SELECT scanned FROM directories WHERE path='Macintosh HD - Data'").fetchone()[0], 0)
        self.assertFalse(self.receiver.published)
        self.assertEqual(self.run_export(), 0)

    def test_legacy_receiver_gets_no_unsupported_batch_command(self):
        (self.main / 'file').write_bytes(b'old protocol remains supported')
        self.assertEqual(self.run_export(), 0)
        self.assertEqual(self.receiver.batch_requests, [])
        self.assertEqual(len(self.receiver.chunks), 1)

    def test_batches_keep_personal_priority_ahead_of_caches_across_a_pause(self):
        self.receiver.batch = True
        home = self.main / 'Users' / 'alice'
        documents = home / 'Documents' / 'nested'
        documents.mkdir(parents=True)
        cache = home / '.cache'
        cache.mkdir()
        for index in range(140):
            (documents / ('file-%04d' % index)).write_bytes(b'personal')
        for index in range(3):
            (cache / ('file-%04d' % index)).write_bytes(b'cache retained')
        self.assertEqual(self.run_export(max_chunks=70), 75)
        copied = [path for path, item in self.receiver.entries.items() if item['kind'] == 'file']
        self.assertEqual(len(copied), 70)
        self.assertTrue(all('/Documents/' in path for path in copied))
        self.assertEqual(self.run_export(), 0)
        ordered = [item['path'] for request in self.receiver.batch_requests
                   for item in request['entries'] if item['kind'] == 'file']
        personal = [i for i, path in enumerate(ordered) if '/Documents/' in path]
        cached = [i for i, path in enumerate(ordered) if '/.cache/' in path]
        self.assertLess(max(personal), min(cached))
        self.assertEqual(self.receiver.counters()['filesCopied'], 143)

    def test_session_end_while_preparing_batch_retains_pending_rows(self):
        self.receiver.batch = True
        (self.main / 'file').write_bytes(b'pending user data')
        with mock.patch.object(EXPORT.ExportEngine, '_pace',
                               side_effect=EXPORT.PauseExport('Session budget ended before sending')):
            self.assertEqual(self.run_export(), 75)
        self.assertEqual(self.receiver.batch_requests, [])
        with self.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM entries WHERE done=1').fetchone()[0], 0)
        self.assertEqual(self.run_export(), 0)

    def test_large_files_keep_chunk_protocol_between_small_file_batches(self):
        self.receiver.batch = True
        (self.main / 'a-small').write_bytes(b'first')
        content = os.urandom(EXPORT.CHUNK_SIZE * 2 + 17)
        (self.main / 'b-large').write_bytes(content)
        (self.main / 'c-small').write_bytes(b'last')
        self.assertEqual(self.run_export(), 0)
        self.assertEqual([offset for path, offset, length in self.receiver.chunks],
                         [0, EXPORT.CHUNK_SIZE, EXPORT.CHUNK_SIZE * 2])
        self.assertEqual(self.receiver.entries['Macintosh HD - Data/b-large']['data'], content)
        self.assertFalse(any(item['path'].endswith('/b-large') for request in self.receiver.batch_requests
                             for item in request['entries']))


PROCESS_RECEIVER = r'''
import json, os, signal, subprocess, sys, time
mode = sys.argv[1]
def reply(value):
    print(json.dumps(value), flush=True)
child = None
if mode == 'stubborn':
    child = subprocess.Popen([sys.executable, '-c',
        'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
reply(dict(ok=True, published=False, bytesCopied=0, filesCopied=0,
    childPid=child.pid if child else None,
    lifecycle=dict(version=1,idleTimeoutSeconds=180,heartbeat=True,close=True)))
if mode == 'blocked-input':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True: time.sleep(1)
heartbeats = 0
for line in sys.stdin.buffer:
    frame = json.loads(line)
    op = frame['op']
    if op == 'heartbeat':
        heartbeats += 1
        reply(dict(ok=True,alive=True))
    elif op == 'chunk':
        reply(dict(ok=True,needData=True,offset=0))
        payload = sys.stdin.buffer.read(frame['length'])
        reply(dict(ok=payload == b'x' * frame['length'],offset=len(payload)))
    elif op == 'close':
        if mode == 'stubborn':
            while True: time.sleep(1)
        reply(dict(ok=True,closed=True))
        break
    elif op == 'status':
        if mode == 'silent-response':
            while True: time.sleep(1)
        if mode == 'slow-response': time.sleep(.12)
        if mode == 'partial-response':
            sys.stdout.write('{"ok":');sys.stdout.flush();time.sleep(.06)
            sys.stdout.write('true,"bytesCopied":0,"filesCopied":0}\n');sys.stdout.flush()
        else: reply(dict(ok=True,bytesCopied=0,filesCopied=0,heartbeats=heartbeats))
'''


class ProcessTestReceiver(EXPORT.SSHReceiver):
    def __init__(self, mode='normal'):
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen([sys.executable, '-u', '-c', PROCESS_RECEIVER, mode],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.errors, bufsize=0, start_new_session=True)
        self._initialize_transport(owns_session=True)


class ReceiverLifecycle(unittest.TestCase):
    def receiver(self, mode='normal'):
        connection = ProcessTestReceiver(mode)
        self.addCleanup(connection.close)
        return connection

    def test_long_source_preparation_keeps_idle_receiver_alive(self):
        with mock.patch.object(EXPORT, 'HEARTBEAT_INTERVAL', .02):
            connection = self.receiver()
            connection.start()
            time.sleep(.12)  # The main sender is occupied reading source metadata.
            status = connection.request(dict(op='status'))
            self.assertGreaterEqual(status['heartbeats'], 3)
            connection.close()
            self.assertEqual(connection.process.returncode, 0)
            self.assertFalse(connection._heartbeat_thread.is_alive())

    def test_heartbeats_never_enter_a_pending_raw_chunk(self):
        with mock.patch.object(EXPORT, 'HEARTBEAT_INTERVAL', .01):
            connection = self.receiver()
            connection.start()
            self.assertTrue(connection.request(dict(op='chunk', length=131072))['needData'])
            time.sleep(.08)
            self.assertEqual(connection.write_chunk(b'x' * 131072)['offset'], 131072)
            self.assertTrue(connection.request(dict(op='status'))['ok'])

    def test_partial_reply_and_long_server_work_are_not_wall_timeout_failures(self):
        for mode in ('partial-response', 'slow-response'):
            with self.subTest(mode=mode), mock.patch.object(EXPORT, 'IO_TIMEOUT', .01):
                connection = self.receiver(mode)
                connection.start()
                self.assertTrue(connection.request(dict(op='status'))['ok'])
                connection.close()

    def test_interruption_breaks_a_silent_reply_wait(self):
        connection = self.receiver('silent-response')
        connection.start()
        result = []
        def wait_for_response():
            try:
                connection.request(dict(op='status'))
            except EXPORT.PauseExport:
                result.append('paused')
        worker = threading.Thread(target=wait_for_response)
        worker.start()
        time.sleep(.03)
        connection.interrupt()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['paused'])
        connection.close()

    def test_failed_pipe_write_cleanup_returns_and_local_job_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(EXPORT, 'IO_TIMEOUT', .04), \
                mock.patch.object(EXPORT, 'CLOSE_TIMEOUT', .05):
            path = Path(directory) / 'job.lock'
            started = time.monotonic()
            with path.open('a+') as lock:
                EXPORT.fcntl.flock(lock, EXPORT.fcntl.LOCK_EX | EXPORT.fcntl.LOCK_NB)
                connection = self.receiver('blocked-input')
                connection.start()
                with self.assertRaises(EXPORT.PauseExport):
                    connection.request(dict(op='batch', data='x' * 1048576))
                connection.close()
            self.assertLess(time.monotonic() - started, 2)
            with path.open('a+') as next_job:
                EXPORT.fcntl.flock(next_job, EXPORT.fcntl.LOCK_EX | EXPORT.fcntl.LOCK_NB)

    def test_bounded_close_kills_only_owned_session_including_stubborn_descendants(self):
        with mock.patch.object(EXPORT, 'CLOSE_TIMEOUT', .05):
            connection = self.receiver('stubborn')
            ready = connection.start()
            child_pid = ready['childPid']
            started = time.monotonic()
            connection.close()
            self.assertLess(time.monotonic() - started, 2)
            self.assertIsNotNone(connection.process.returncode)
            for _ in range(20):
                result = subprocess.run(['/bin/ps', '-p', str(child_pid), '-o', 'stat='],
                                        stdout=subprocess.PIPE, text=True)
                if not result.stdout.strip() or result.stdout.strip().startswith('Z'):
                    break
                time.sleep(.02)
            else:
                self.fail('Owned ProxyCommand descendant survived shutdown')

    def test_group_permission_error_falls_back_to_owned_pid_without_masking_pause(self):
        connection = self.receiver()
        connection.start()
        with mock.patch.object(EXPORT.os, 'killpg', side_effect=PermissionError('group already gone')):
            connection.interrupt()
            connection.close()
        self.assertFalse(connection._owns_session)
        self.assertIsNotNone(connection.process.returncode)


class PHPTestReceiver(EXPORT.SSHReceiver):
    """Use the same framed transport against isolated PHP fixtures, locally or SSH."""
    def __init__(self, args, identity):
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            RECEIVER_TESTS.php_command(Path(args.receiver_script).read_text(), '--',
                                       args.root, args.snapshot, identity),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            bufsize=0, start_new_session=True)
        self._initialize_transport(owns_session=True)


@unittest.skipUnless(RECEIVER_TESTS.SSH or shutil.which(RECEIVER_TESTS.PHP),
                     'PHP CLI is needed (or set PORTABLE_RECEIVER_SSH)')
class ResumableExportIntegration(unittest.TestCase):
    fs = RECEIVER_TESTS.PortableReceiver.fs

    def test_real_receiver_resumes_chunks_and_publishes_browsable_files(self):
        with tempfile.TemporaryDirectory(prefix='pirouter-engine-test.') as directory:
            environment = mock.patch.dict(os.environ, {'PIROUTER_URLS': '', 'PIROUTER_TOKEN': '', 'PIROUTER_REQUEST_ID': ''})
            environment.start()
            self.addCleanup(environment.stop)
            local = Path(directory)
            if RECEIVER_TESTS.SSH:
                self.root = '/tmp/pirouter-receiver-test.' + uuid.uuid4().hex
            else:
                self.root = str(local / 'pirouter-receiver-test.destination')
            self.fs('mkdir')
            self.addCleanup(lambda: self.fs('remove'))
            source = local / 'source'
            main = source / 'Macintosh HD - Data' / 'Users' / 'test'
            main.mkdir(parents=True)
            chunk_size = 256 * 1024
            content = os.urandom(chunk_size * 2 + 83)
            (main / 'large-vm.hds').write_bytes(content)
            (main / 'notes.txt').write_bytes(b'actual readable user files')
            os.symlink('notes.txt', main / 'notes-link')
            (source / '.exclusions.plist').write_bytes(b'Time Machine internal metadata')
            args = Namespace(source=str(source), snapshot=SNAPSHOT,
                             state_dir=str(local / 'state'), host='test-transport',
                             root=self.root, ssh_key='test-transport',
                             receiver_script=str(ROOT / 'scripts/portable-export-receiver.php'),
                             bandwidth_kb=1024 * 1024, session_seconds=0, max_chunks=1)
            def run_engine():
                engine = EXPORT.ExportEngine(args)
                engine.transport_factory = lambda: PHPTestReceiver(args, engine.identity)
                with redirect_stdout(io.StringIO()):
                    return engine.run()
            with mock.patch.object(EXPORT, 'CHUNK_SIZE', chunk_size):
                self.assertEqual(run_engine(), 75)
                self.assertFalse(self.fs('exists', 'snapshots/' + SNAPSHOT))
                args.max_chunks = None
                self.assertEqual(run_engine(), 0)
            prefix = 'snapshots/' + SNAPSHOT + '/Macintosh HD - Data/Users/test/'
            self.assertEqual(self.fs('hash', prefix + 'large-vm.hds'), hashlib.sha256(content).hexdigest())
            self.assertEqual(self.fs('readlink', prefix + 'notes-link'), 'notes.txt')
            self.assertTrue(self.fs('exists', 'snapshots/' + SNAPSHOT + '/.pirouter-complete'))


if __name__ == '__main__':
    unittest.main()
