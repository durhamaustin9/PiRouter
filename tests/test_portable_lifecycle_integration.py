"""Exercise connection lifetime against the real PHP receiver in private fixtures.

Set PORTABLE_RECEIVER_SSH as in test_portable_receiver.py for remote PHP. Short
timeouts and simulated slow storage exist only in the source supplied to the
fixture process; installed scripts and production destinations are never changed.
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


EXPORT = load_module('lifecycle_integration_export', ROOT / 'scripts/resumable-portable-export.py')
HARNESS = load_module('lifecycle_integration_harness', ROOT / 'tests/test_portable_receiver.py')
SNAPSHOT = HARNESS.SNAPSHOT
PARTIAL = HARNESS.PARTIAL
FINAL = HARNESS.FINAL
TIMEOUT = 1.0


class HeldOpenInput:
    """Model a transport retaining the pipe writer after client.close()."""
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        # The explicit protocol close must release the server lock without EOF.
        pass


@unittest.skipUnless(HARNESS.SSH or shutil.which(HARNESS.PHP),
                     'PHP CLI is needed (or set PORTABLE_RECEIVER_SSH)')
class PortableLifecycleIntegration(unittest.TestCase):
    fs = HARNESS.PortableReceiver.fs
    batch_entry = staticmethod(HARNESS.PortableReceiver.batch_entry)

    def setUp(self):
        HARNESS.PortableReceiver.setUp(self)
        self.addCleanup(self.cleanup_destination)
        self.local = tempfile.TemporaryDirectory(prefix='pirouter-lifecycle-integration.')
        self.addCleanup(self.local.cleanup)
        self.source = Path(self.local.name) / (SNAPSHOT + '.backup')
        self.documents = self.source / 'Macintosh HD - Data/Users/test/Documents'
        self.documents.mkdir(parents=True)
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
        for connection, original_input in self.transports:
            if not connection.errors.closed:
                connection.close()
            if not original_input.closed:
                original_input.close()

    def receiver_code(self, timeout=TIMEOUT, journal=False):
        code = (ROOT / 'scripts/portable-export-receiver.php').read_text()
        needle = 'const PROTOCOL_IDLE_SECONDS = 180.0;'
        self.assertEqual(code.count(needle), 1, 'Update fixture timeout injection for receiver changes')
        code = code.replace(needle, 'const PROTOCOL_IDLE_SECONDS = %.3f;' % timeout)
        if journal:
            needle = "$op = $frame['op'] ?? null;"
            self.assertEqual(code.count(needle), 1)
            code = code.replace(needle, needle + "\n            file_put_contents($root . '/.lifecycle-test-events', json_encode($op) . \"\\n\", FILE_APPEND | LOCK_EX);")
        return code

    def session(self, code=None, source=HARNESS.SOURCE):
        return HARNESS.Session(self, source=source,
                               code=self.receiver_code() if code is None else code)

    def assert_ok(self, reply):
        self.assertTrue(reply.get('ok'), reply)
        return reply

    def assert_expires(self, connection):
        self.assertFalse(connection.process.stdin.closed)
        connection.process.wait(timeout=8)
        self.assertEqual(connection.process.returncode, 75,
                         connection.process.stderr.read().decode(errors='replace'))
        self.assertEqual(connection.process.stdout.read(), b'',
                         'An incomplete transfer must not produce a success ACK')
        self.assertFalse(connection.process.stdin.closed,
                         'The receiver must expire while its input pipe remains open')

    def checkpoint(self):
        return self.fs('checkpoint', PARTIAL)

    def seed_progress(self, connection):
        complete = b'already durably complete'
        self.assert_ok(connection.begin(complete, path='Data/complete'))
        self.assert_ok(connection.chunk(complete, path='Data/complete')[1])
        self.assert_ok(connection.finish('Data/complete'))
        payload = b'a' * 4096 + b'b' * 4096
        self.assert_ok(connection.begin(payload))
        self.assertEqual(self.assert_ok(connection.chunk(payload[:4096])[1])['offset'], 4096)
        return payload, len(complete) + 4096

    def args(self):
        return Namespace(source=str(self.source), snapshot=SNAPSHOT,
                         state_dir=str(Path(self.local.name) / 'state'),
                         host='isolated-php-fixture', root=self.root, ssh_key='unused',
                         receiver_script=str(ROOT / 'scripts/portable-export-receiver.php'),
                         bandwidth_kb=1024 * 1024, session_seconds=0,
                         max_chunks=None, proxy_command='')

    def transport(self, args, identity, code, held_open=False):
        errors = tempfile.TemporaryFile()
        process = subprocess.Popen(
            HARNESS.php_command(code, '--', self.root, SNAPSHOT, identity),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
            bufsize=0, start_new_session=True)
        original_input = process.stdin
        if held_open:
            process.stdin = HeldOpenInput(original_input)
        # Test SSH channels share an existing control master. Exercise the real
        # lifecycle initializer without claiming production process-group ownership.
        connection = EXPORT.SSHReceiver.__new__(EXPORT.SSHReceiver)
        connection.process = process
        connection.errors = errors
        connection._initialize_transport()
        self.transports.append((connection, original_input))
        return connection

    def events(self):
        if not self.fs('exists', '.lifecycle-test-events'):
            return []
        data = base64.b64decode(self.fs('read', '.lifecycle-test-events'))
        return [json.loads(line) for line in data.splitlines()]

    def run_engine(self, code, delay=0, io_timeout=8.0):
        args = self.args()
        engine = EXPORT.ExportEngine(args)
        engine.transport_factory = lambda: self.transport(args, engine.identity, code)
        if delay:
            original = engine._pace

            def slow_source_preparation(length):
                time.sleep(delay)
                return original(length)

            engine._pace = slow_source_preparation
        with mock.patch.object(EXPORT, 'HEARTBEAT_INTERVAL', 0.1), \
                mock.patch.object(EXPORT, 'IO_TIMEOUT', io_timeout), \
                redirect_stdout(io.StringIO()):
            return engine.run()

    def test_idle_open_pipe_expires_and_next_receiver_acquires_lock(self):
        connection = self.session()
        self.assertEqual(connection.ready['lifecycle']['version'], 1)
        self.assert_expires(connection)
        next_connection = self.session()
        self.assert_ok(next_connection.ready)
        self.assertEqual(next_connection.ready['bytesCopied'], 0)
        next_connection.close()

    def test_incomplete_json_frame_expires_without_losing_saved_progress(self):
        connection = self.session()
        payload, saved_bytes = self.seed_progress(connection)
        connection.write(b'{"op":"begin","path":"Data/unacknowledged')
        self.assert_expires(connection)
        self.assertEqual(self.checkpoint()['bytes_copied'], saved_bytes)
        self.assertEqual(self.checkpoint()['files_copied'], 1)
        resumed = self.session()
        self.assertEqual(self.assert_ok(resumed.begin(payload))['offset'], 4096)
        self.assertFalse(self.fs('exists', PARTIAL + '/Data/unacknowledged'))
        resumed.close()

    def test_partial_raw_chunk_expires_and_retains_previous_verified_offset(self):
        connection = self.session()
        payload, saved_bytes = self.seed_progress(connection)
        offer = dict(op='chunk', path='Data/file', offset=4096, length=4096,
                     sha256=hashlib.sha256(payload[4096:]).hexdigest())
        self.assertTrue(self.assert_ok(connection.command(offer))['needData'])
        connection.write(payload[4096:5120])
        self.assert_expires(connection)
        self.assertEqual(self.checkpoint()['bytes_copied'], saved_bytes)
        self.assertEqual(self.checkpoint()['files_copied'], 1)
        resumed = self.session()
        self.assertEqual(self.assert_ok(resumed.begin(payload))['offset'], 4096)
        self.assertEqual(self.assert_ok(resumed.chunk(payload[4096:], offset=4096)[1])['offset'], len(payload))
        self.assert_ok(resumed.finish())
        resumed.close()
        self.assertEqual(self.fs('hash', PARTIAL + '/Data/file'), hashlib.sha256(payload).hexdigest())

    def test_blocked_output_expires_and_releases_destination_lock(self):
        code = self.receiver_code()
        needle = "elseif ($op === 'status') reply(['ok' => true] + counters($db));"
        self.assertEqual(code.count(needle), 1)
        code = code.replace(needle, "elseif ($op === 'status') reply(['ok' => true, 'fixturePadding' => str_repeat('x', 32 * 1024 * 1024)] + counters($db));")
        connection = self.session(code)
        connection.send(dict(op='status'))
        # Leave stdout unread, filling both SSH's window and the local pipe.
        # A second error reply may itself wait for another I/O timeout.
        time.sleep(4 * TIMEOUT)
        next_connection = self.session()
        self.assert_ok(next_connection.ready)
        self.assertEqual(next_connection.ready['bytesCopied'], 0)
        next_connection.close()
        connection.close()

    def test_slow_active_batch_processing_is_not_an_idle_connection(self):
        code = self.receiver_code()
        needle = 'function syncFile($handle): void {'
        self.assertEqual(code.count(needle), 1)
        code = code.replace(needle, needle + '\n    static $delayed = false; if (!$delayed) { $delayed = true; usleep(2500000); }')
        data = b'preserved through slow fsync'
        (self.documents / 'slow-file').write_bytes(data)
        started = time.monotonic()
        # Connection write deadlines must not become a wall-clock limit on the
        # real client's wait for an active, durable batch operation.
        self.assertEqual(self.run_engine(code, io_timeout=0.3), 0)
        self.assertGreater(time.monotonic() - started, 2 * TIMEOUT)
        relative = str((self.documents / 'slow-file').relative_to(self.source))
        self.assertEqual(self.fs('hash', FINAL + '/' + relative), hashlib.sha256(data).hexdigest())

    def test_client_heartbeats_keep_long_source_preparation_alive(self):
        data = b'a small useful document'
        (self.documents / 'document').write_bytes(data)
        code = self.receiver_code(journal=True)
        self.assertEqual(self.run_engine(code, delay=2.5), 0)
        self.assertGreaterEqual(self.events().count('heartbeat'), 5)
        relative = str((self.documents / 'document').relative_to(self.source))
        self.assertEqual(self.fs('hash', FINAL + '/' + relative), hashlib.sha256(data).hexdigest())

    def test_heartbeats_never_interleave_raw_payload_or_chunk_preparation(self):
        # Exceed the negotiated batch ceiling to force the unchanged raw-chunk path,
        # while keeping this lifecycle test independent of bulk WAN throughput.
        data = os.urandom(EXPORT.BATCH_MAX_FILE_BYTES + 73)
        (self.documents / 'large-document').write_bytes(data)
        self.assertEqual(self.run_engine(self.receiver_code(journal=True), delay=2.5), 0)
        self.assertGreaterEqual(self.events().count('heartbeat'), 5)
        self.assertEqual(self.events().count('chunk'), 1)
        relative = str((self.documents / 'large-document').relative_to(self.source))
        self.assertEqual(self.fs('hash', FINAL + '/' + relative), hashlib.sha256(data).hexdigest())

    def test_client_close_releases_lock_without_transport_pipe_eof(self):
        args = self.args()
        identity = EXPORT.source_identity(self.source, SNAPSHOT)
        connection = self.transport(args, identity, self.receiver_code(journal=True), held_open=True)
        self.assert_ok(connection.start())
        original_input = self.transports[-1][1]
        connection.close()
        self.assertFalse(original_input.closed, 'The fixture must retain its input writer')
        self.assertEqual(connection.process.poll(), 0,
                         'A healthy explicit close should let PHP/SSH exit normally')
        self.assertIn('close', self.events())
        next_connection = self.session(source=identity)
        self.assert_ok(next_connection.ready)
        next_connection.close()

    def test_legacy_sender_and_receiver_remain_compatible(self):
        # The old sender only understands begin/chunk/finish and closes stdin.
        connection = self.session()
        data = b'legacy sender preserved'
        self.assert_ok(connection.begin(data))
        self.assert_ok(connection.chunk(data)[1])
        self.assert_ok(connection.finish())
        connection.close()
        self.assertEqual(self.checkpoint()['bytes_copied'], len(data))
        # Use another snapshot/source fixture so the real new sender can finish
        # against a receiver that advertises no lifecycle operations.
        self.fs('remove', PARTIAL)
        (self.documents / 'legacy-document').write_bytes(b'new sender, old receiver')
        code = self.receiver_code(journal=True)
        needle = '+ lifecycleCapabilities()'
        self.assertGreaterEqual(code.count(needle), 1)
        code = code.replace(needle, '')
        self.assertEqual(self.run_engine(code), 0)
        self.assertNotIn('heartbeat', self.events())
        self.assertNotIn('close', self.events())


if __name__ == '__main__':
    unittest.main()
