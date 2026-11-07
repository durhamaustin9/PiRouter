"""Test batch upgrades and interrupted transfers against the actual PHP receiver.

Set PORTABLE_RECEIVER_SSH as documented in test_portable_receiver.py when PHP
is remote. All destination writes stay in isolated /tmp fixture roots. Optional
PORTABLE_BATCH_BENCHMARK=1 compares legacy and batch request counts with injected
round-trip delay; it never uses production backup data.
"""
from argparse import Namespace
import base64
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORT = load_module('batch_integration_export', ROOT / 'scripts/resumable-portable-export.py')
HARNESS = load_module('batch_integration_receiver', ROOT / 'tests/test_portable_receiver.py')
SNAPSHOT = HARNESS.SNAPSHOT
VOLUME = 'Macintosh HD - Data'
DOCUMENTS = VOLUME + '/Users/test/Documents'


def populate_tiny_fixture(source, count=96):
    """A deterministic small-file workload, including nested empty directories."""
    documents = source / DOCUMENTS
    documents.mkdir(parents=True, exist_ok=True)
    expected = {}
    for number in range(count):
        directory = documents / ('group%02d' % (number % 6))
        directory.mkdir(exist_ok=True)
        path = directory / ('item%04d' % number)
        data = ('tiny-file-%04d\n' % number).encode() * (1 + number % 17)
        path.write_bytes(data)
        expected[str(path.relative_to(source))] = data
    for suffix in ('empty', 'nested/empty/leaf'):
        (documents / suffix).mkdir(parents=True, exist_ok=True)
    return expected


class ActualPHPTransport(EXPORT.SSHReceiver):
    """Keep production framing; optionally hide capability or lose a durable ACK."""
    def __init__(self, fixture, args, identity, legacy=False, lost_batch_ack=False, rtt=0):
        self.fixture = fixture
        self.legacy = legacy
        self.lost_batch_ack = lost_batch_ack
        self.rtt = rtt
        self.messages = []
        self.rtt_events = 0
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            HARNESS.php_command(Path(args.receiver_script).read_text(), '--',
                                args.root, args.snapshot, identity),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
        self._initialize_transport()
        fixture.transports.append(self)

    def start(self):
        ready = super().start()
        if self.legacy:
            ready.pop('batch', None)
        return ready

    def _round_trip(self):
        self.rtt_events += 1
        if self.rtt:
            time.sleep(self.rtt)

    def request(self, message):
        self._round_trip()
        self.messages.append(message)
        reply = super().request(message)
        if (message['op'] == 'batch' and self.lost_batch_ack and
                any(entry['kind'] == 'file' for entry in message['entries'])):
            # The receiver has durably committed, but the engine has not seen
            # its acknowledgement and must not advance its local entry flags.
            self.lost_batch_ack = False
            self.close()
            raise EXPORT.PauseExport('Injected disconnect after remote batch commit')
        return reply

    def write_chunk(self, data):
        self._round_trip()
        return super().write_chunk(data)


class TrackedReader:
    def __init__(self, handle, reads):
        self.handle = handle
        self.reads = reads

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def read(self, length):
        self.reads.append((self.handle.tell(), length))
        return self.handle.read(length)


@unittest.skipUnless(HARNESS.SSH or shutil.which(HARNESS.PHP),
                     'PHP CLI is needed (or set PORTABLE_RECEIVER_SSH)')
class PortableBatchIntegration(unittest.TestCase):
    # Reuse the fixture helpers without inheriting all receiver unit tests.
    fs = HARNESS.PortableReceiver.fs

    def setUp(self):
        HARNESS.PortableReceiver.setUp(self)
        self.addCleanup(self.cleanup_destination)
        self.local = tempfile.TemporaryDirectory(prefix='pirouter-batch-integration.')
        self.addCleanup(self.local.cleanup)
        self.source = Path(self.local.name) / (SNAPSHOT + '.backup')
        self.documents = self.source / DOCUMENTS
        self.documents.mkdir(parents=True)
        self.state = Path(self.local.name) / 'state'
        self.transports = []
        self.addCleanup(self.close_transports)
        patch = mock.patch.dict(os.environ, {
            'PIROUTER_URLS': '', 'PIROUTER_TOKEN': '', 'PIROUTER_REQUEST_ID': '',
        })
        patch.start()
        self.addCleanup(patch.stop)

    def cleanup_destination(self):
        HARNESS.PortableReceiver.tearDown(self)

    def close_transports(self):
        for transport in self.transports:
            if not transport.errors.closed:
                transport.close()

    def args(self, **overrides):
        return Namespace(**dict(dict(
            source=str(self.source), snapshot=SNAPSHOT, state_dir=str(self.state),
            host='isolated-php-fixture', root=self.root, ssh_key='unused',
            receiver_script=str(ROOT / 'scripts/portable-export-receiver.php'),
            bandwidth_kb=1024 * 1024, session_seconds=0, max_chunks=None,
            proxy_command=''), **overrides))

    def run_engine(self, legacy=False, lost_batch_ack=False, rtt=0, **overrides):
        args = self.args(**overrides)
        engine = EXPORT.ExportEngine(args)
        engine.transport_factory = lambda: ActualPHPTransport(
            self, args, engine.identity, legacy=legacy,
            lost_batch_ack=lost_batch_ack, rtt=rtt)
        with redirect_stdout(io.StringIO()):
            return engine.run()

    def session(self):
        return HARNESS.Session(self, source=EXPORT.source_identity(self.source, SNAPSHOT))

    def local_rows(self):
        with sqlite3.connect(str(self.state / (SNAPSHOT + '.sqlite3'))) as database:
            rows = database.execute('SELECT path,done,offset FROM entries').fetchall()
            counters = dict(database.execute('SELECT key,value FROM job'))
        return {path: (done, offset) for path, done, offset in rows}, counters

    def remote_counts(self):
        connection = self.session()
        result = connection.ready
        connection.close()
        return result

    def batch_messages(self):
        return [message for connection in self.transports for message in connection.messages
                if message['op'] == 'batch']

    def file_frame(self, path, data):
        info = (self.source / path).lstat()
        record = EXPORT.metadata(path, info)
        return dict(path=path, kind='file', identity=record['identity'],
                    size=len(data), mode=record['mode'],
                    mtime=info.st_mtime_ns // 1_000_000_000,
                    data=base64.b64encode(data).decode(),
                    sha256=hashlib.sha256(data).hexdigest())

    def assert_batch_bounds(self):
        batches = self.batch_messages()
        self.assertTrue(batches, 'The actual receiver did not negotiate batching')
        for message in batches:
            self.assertGreater(len(message['entries']), 0)
            self.assertLessEqual(len(message['entries']), 64)
            payload = 0
            for entry in message['entries']:
                if entry['kind'] == 'file':
                    data = base64.b64decode(entry['data'], validate=True)
                    self.assertLessEqual(len(data), EXPORT.BATCH_MAX_FILE_BYTES)
                    payload += len(data)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            self.assertLessEqual(payload, EXPORT.BATCH_MAX_PAYLOAD_BYTES)

    def test_upgrade_keeps_nonzero_legacy_progress_and_large_chunk_offset(self):
        existing = b'already verified before the batch upgrade'
        large = os.urandom(EXPORT.CHUNK_SIZE * 2 + 37)
        (self.documents / '00-existing').write_bytes(existing)
        (self.documents / '01-large').write_bytes(large)
        for number in range(12):
            (self.documents / ('02-small-%02d' % number)).write_bytes(b'small' * (number + 1))
        self.assertEqual(self.run_engine(legacy=True, max_chunks=2), 75)
        saved, counters = self.local_rows()
        self.assertEqual(saved[DOCUMENTS + '/00-existing'], (1, len(existing)))
        self.assertEqual(saved[DOCUMENTS + '/01-large'], (0, EXPORT.CHUNK_SIZE))
        self.assertEqual(int(counters['bytes_copied']), len(existing) + EXPORT.CHUNK_SIZE)
        self.assertEqual(self.remote_counts()['bytesCopied'], len(existing) + EXPORT.CHUNK_SIZE)
        reads = []
        original_fdopen = os.fdopen
        inode = (self.documents / '01-large').stat().st_ino

        def tracking_open(descriptor, mode):
            tracked = os.fstat(descriptor).st_ino == inode
            handle = original_fdopen(descriptor, mode)
            return TrackedReader(handle, reads) if tracked else handle

        with mock.patch.object(EXPORT.os, 'fdopen', side_effect=tracking_open):
            self.assertEqual(self.run_engine(), 0)
        self.assertTrue(reads)
        self.assertTrue(all(offset >= EXPORT.CHUNK_SIZE for offset, length in reads), reads)
        self.assert_batch_bounds()
        complete = self.remote_counts()
        self.assertEqual(complete['filesCopied'], 14)
        self.assertEqual(complete['bytesCopied'], len(existing) + len(large) + sum(5 * (n + 1) for n in range(12)))
        self.assertEqual(self.fs('hash', HARNESS.FINAL + '/' + DOCUMENTS + '/01-large'),
                         hashlib.sha256(large).hexdigest())

    def test_lost_batch_ack_reuses_committed_files_without_double_counting(self):
        expected = populate_tiny_fixture(self.source, count=18)
        self.assertEqual(self.run_engine(lost_batch_ack=True), 75)
        first = self.batch_messages()[-1]
        saved, counters = self.local_rows()
        self.assertTrue(any(not saved[entry['path']][0] for entry in first['entries']))
        committed = self.remote_counts()
        self.assertFalse(committed.get('published'))
        self.assertEqual(self.run_engine(), 0)
        complete = self.remote_counts()
        self.assertEqual(complete['filesCopied'], len(expected))
        self.assertEqual(complete['bytesCopied'], sum(map(len, expected.values())))
        for path, data in list(expected.items())[:3]:
            self.assertEqual(self.fs('hash', HARNESS.FINAL + '/' + path), hashlib.sha256(data).hexdigest())
        self.assertTrue(self.fs('exists', HARNESS.FINAL + '/' + DOCUMENTS + '/nested/empty/leaf'))
        self.assert_batch_bounds()

    def test_large_and_bounded_small_batches_publish_together(self):
        expected = populate_tiny_fixture(self.source, count=70)
        for number in range(5):
            path = self.documents / ('max-small-%d' % number)
            path.write_bytes(bytes([number]) * 65536)
            expected[str(path.relative_to(self.source))] = path.read_bytes()
        large = b'chunked-not-batched' * (EXPORT.BATCH_MAX_FILE_BYTES // 19 + 1)
        large_path = self.documents / 'larger-than-small-limit'
        large_path.write_bytes(large)
        expected[str(large_path.relative_to(self.source))] = large
        self.assertEqual(self.run_engine(), 0)
        self.assert_batch_bounds()
        batched_paths = {entry['path'] for message in self.batch_messages() for entry in message['entries']}
        self.assertNotIn(str(large_path.relative_to(self.source)), batched_paths)
        complete = self.remote_counts()
        self.assertEqual(complete['filesCopied'], len(expected))
        self.assertEqual(complete['bytesCopied'], sum(map(len, expected.values())))
        self.assertTrue(any(message['op'] == 'chunk' for connection in self.transports for message in connection.messages))

    def test_medium_documents_batch_and_resume_after_a_lost_durable_ack(self):
        expected = {}
        for index, size in enumerate((300 * 1024, EXPORT.BATCH_MAX_FILE_BYTES)):
            path = self.documents / ('document-%d.pdf' % index)
            data = os.urandom(size)
            path.write_bytes(data)
            expected[str(path.relative_to(self.source))] = data
        self.assertEqual(self.run_engine(lost_batch_ack=True), 75)
        self.assertEqual(self.run_engine(), 0)
        self.assert_batch_bounds()
        batched = {entry['path'] for message in self.batch_messages() for entry in message['entries']}
        self.assertTrue(set(expected).issubset(batched))
        self.assertFalse(any(message['op'] == 'chunk' for connection in self.transports
                             for message in connection.messages))
        for path, data in expected.items():
            self.assertEqual(self.fs('hash', HARNESS.FINAL + '/' + path), hashlib.sha256(data).hexdigest())
        complete = self.remote_counts()
        self.assertEqual((complete['bytesCopied'], complete['filesCopied']), (sum(map(len, expected.values())), 2))

    def test_incomplete_batch_frame_preserves_prior_commits_and_resumes(self):
        path = DOCUMENTS + '/saved'
        data = b'saved before an incomplete JSON frame'
        (self.source / path).write_bytes(data)
        (self.documents / 'next').write_bytes(b'retry after partial framing')
        connection = self.session()
        frame = dict(op='batch', entries=[self.file_frame(path, data)])
        self.assertTrue(connection.command(frame)['batchComplete'])
        connection.write(b'{"op":"batch","entries":[{"path":"unfinished')
        connection.close()
        self.assertEqual(self.remote_counts()['bytesCopied'], len(data))
        self.assertEqual(self.run_engine(), 0)
        self.assertEqual(self.remote_counts()['filesCopied'], 2)

    def test_invalid_batch_does_not_publish_or_escape_and_regular_retry_succeeds(self):
        data = b'valid ordinary file'
        path = DOCUMENTS + '/ordinary'
        (self.source / path).write_bytes(data)
        metadata = self.file_frame(path, data)
        connection = self.session()
        response = connection.command(dict(op='batch', entries=[metadata, dict(metadata, path='../escape')]))
        self.assertFalse(response['ok'])
        connection.close()
        self.assertFalse(self.fs('exists', HARNESS.PARTIAL + '/' + path))
        self.assertFalse(self.fs('exists', 'escape'))
        self.assertFalse(self.fs('exists', 'snapshots/escape'))
        self.assertEqual(self.run_engine(), 0)
        self.assertEqual(self.remote_counts()['filesCopied'], 1)

    def test_batch_preserves_symlinks_and_detaches_historical_hardlinks(self):
        data = b'new contents'
        old = b'old contents'
        path = DOCUMENTS + '/document'
        (self.source / path).write_bytes(data)
        os.symlink('document', self.documents / 'alias')
        old_path = 'snapshots/1999-12-16-120000/' + path
        self.fs('write', old_path, old)
        self.fs('hardlink', HARNESS.PARTIAL + '/' + path, old_path)
        before = self.fs('stat', old_path)
        self.assertEqual(self.run_engine(), 0)
        self.assertEqual(self.fs('hash', old_path), hashlib.sha256(old).hexdigest())
        after = self.fs('stat', old_path)
        self.assertEqual((after['mode'], after['mtime']), (before['mode'], before['mtime']))
        self.assertEqual(self.fs('hash', HARNESS.FINAL + '/' + path), hashlib.sha256(data).hexdigest())
        self.assertEqual(self.fs('readlink', HARNESS.FINAL + '/' + DOCUMENTS + '/alias'), 'document')

    @unittest.skipUnless(os.environ.get('PORTABLE_BATCH_BENCHMARK') == '1',
                         'Set PORTABLE_BATCH_BENCHMARK=1 for the isolated RTT comparison')
    def test_tiny_fixture_reduces_round_trips_with_actual_php(self):
        expected = populate_tiny_fixture(self.source, count=96)
        self.assertEqual(self.run_engine(legacy=True, rtt=0.005), 0)
        legacy_events = sum(connection.rtt_events for connection in self.transports)
        self.close_transports()
        self.fs('remove')
        self.fs('mkdir')
        self.state = Path(self.local.name) / 'batch-state'
        self.transports = []
        self.assertEqual(self.run_engine(rtt=0.005), 0)
        batch_events = sum(connection.rtt_events for connection in self.transports)
        self.assertLess(batch_events * 4, legacy_events)
        self.assertEqual(self.remote_counts()['filesCopied'], len(expected))
        print(json.dumps(dict(legacyRoundTrips=legacy_events, batchRoundTrips=batch_events,
                              injectedRttSeconds=0.005, files=len(expected))), flush=True)


if __name__ == '__main__':
    unittest.main()
