"""Verify optional SSH routing and a fixed-destination, bounded stream relay."""
from argparse import Namespace
import importlib.util
import os
import shlex
from pathlib import Path
import queue
import selectors
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORT = load_module('proxy_export', ROOT / 'scripts/resumable-portable-export.py')
RELAY = load_module('portable_relay', ROOT / 'scripts/portable-export-relay.py')


class ProxyConfiguration(unittest.TestCase):
    def ssh_arguments(self, proxy=None):
        args = Namespace(receiver_script=str(ROOT / 'scripts/portable-export-receiver.php'),
                         root='/mnt/user/test', snapshot='2000-01-03-120000',
                         ssh_key='/private/key with spaces', host='backup@example')
        if proxy is not None:
            args.proxy_command = proxy
        with mock.patch.object(EXPORT.subprocess, 'Popen') as start:
            receiver = EXPORT.SSHReceiver(args, 'a' * 64)
            self.addCleanup(receiver.errors.close)
            return start.call_args.args[0]

    def test_direct_connection_has_no_proxy_option_by_default(self):
        arguments = self.ssh_arguments()
        self.assertFalse(any(value.startswith('ProxyCommand=') for value in arguments))
        self.assertIn('ConnectTimeout=30', arguments)
        self.assertEqual(arguments[-2], 'backup@example')

    def test_configured_proxy_is_passed_as_one_exact_ssh_argument(self):
        proxy = '/usr/bin/ssh -T -i "/a key" relay@example fixed-only'
        arguments = self.ssh_arguments(proxy)
        self.assertEqual(arguments.count('ProxyCommand=' + proxy), 1)
        index = arguments.index('ProxyCommand=' + proxy)
        self.assertEqual(arguments[index - 1], '-o')
        self.assertEqual(arguments[-2], 'backup@example')
        self.assertNotIn('shell', str(arguments))

    def test_ssh_stderr_diagnostics_cannot_corrupt_receiver_json(self):
        args = Namespace(receiver_script=str(ROOT / 'scripts/portable-export-receiver.php'),
                         root='/mnt/user/test', snapshot='2000-01-03-120000',
                         ssh_key='fixture', host='fixture')
        real_popen = subprocess.Popen

        def noisy_ssh(arguments, **options):
            return real_popen([sys.executable, '-c',
                'import sys; sys.stderr.write("SSH locale warning\\n"); '
                'print(\'{"ok":true,"published":false,"bytesCopied":123,"filesCopied":1}\')'],
                **options)

        with mock.patch.object(EXPORT.subprocess, 'Popen', side_effect=noisy_ssh):
            receiver = EXPORT.SSHReceiver(args, 'a' * 64)
        try:
            self.assertEqual(receiver.start()['bytesCopied'], 123)
        finally:
            receiver.close()

    def test_installer_shell_quotes_proxy_without_executing_it_on_config_load(self):
        source = (ROOT / 'scripts/install-portable-export-macos.sh').read_text()
        block = source[source.index('# This file is sourced by Bash.'):
                       source.index('install -m 0600 "$config_temp" "$config_file"')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'must-not-exist'
            proxy = f'/usr/bin/ssh -i "a key" relay $(touch "{marker}") `echo not-run`\nnext line'
            script = '''
config_temp="$CONFIG_OUTPUT"
pirouter_url=http://test
pirouter_local_url=http://local
pirouter_token=fixture
export_host=backup@example
export_root=/mnt/user/test
export_ssh_key='/a key'
export_ssh_proxy_command="$PROXY_VALUE"
''' + block + '\nsource "$config_temp"\nprintf \'%s\' "$EXPORT_SSH_PROXY_COMMAND"\n'
            result = subprocess.run(['/bin/bash', '-c', script], env={
                **os.environ, 'CONFIG_OUTPUT': str(root / 'config'), 'PROXY_VALUE': proxy,
            }, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, proxy)
            self.assertFalse(marker.exists())

    def test_installer_preserves_existing_proxy_and_allows_explicit_clear(self):
        source = (ROOT / 'scripts/install-portable-export-macos.sh').read_text()
        block = source[source.index('requested_pirouter_url='):
                       source.index('if [[ -z "$pirouter_url" ]]')]
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'config'
            filename.write_text("EXPORT_SSH_PROXY_COMMAND='existing proxy command'\n")
            environment = {key: value for key, value in os.environ.items()
                           if key != 'EXPORT_SSH_PROXY_COMMAND'}
            environment['CONFIG_FILE'] = str(filename)
            script = 'config_file="$CONFIG_FILE"\n' + block + '\nprintf \'%s\' "$export_ssh_proxy_command"\n'
            for override, expected in ((None, 'existing proxy command'), ('', ''), ('new proxy', 'new proxy')):
                with self.subTest(override=override):
                    configured = dict(environment)
                    if override is not None:
                        configured['EXPORT_SSH_PROXY_COMMAND'] = override
                    result = subprocess.run(['/bin/bash', '-c', script], env=configured,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)

    def test_installer_defaults_to_dedicated_share_without_redirecting_existing_exports(self):
        source = (ROOT / 'scripts/install-portable-export-macos.sh').read_text()
        block = source[source.index('requested_pirouter_url='):
                       source.index('if [[ -z "$pirouter_url" ]]')]
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'config'
            environment = {key: value for key, value in os.environ.items() if key != 'EXPORT_ROOT'}
            environment['CONFIG_FILE'] = str(filename)
            script = 'config_file="$CONFIG_FILE"\n' + block + '\nprintf \'%s\' "$export_root"\n'
            cases = [(None, None, '/mnt/user/time-machine-austin'),
                     ('/mnt/user/existing-export', None, '/mnt/user/existing-export'),
                     ('/mnt/user/existing-export', '/mnt/user/time-machine-austin',
                      '/mnt/user/time-machine-austin')]
            for saved, override, expected in cases:
                with self.subTest(saved=saved, override=override):
                    filename.write_text('' if saved is None else 'EXPORT_ROOT=' + shlex.quote(saved) + '\n')
                    configured = dict(environment)
                    if override is not None:
                        configured['EXPORT_ROOT'] = override
                    result = subprocess.run(['/bin/bash', '-eu', '-c', script], env=configured,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)


class StreamRelay(unittest.TestCase):
    def start(self, handler, original_command=''):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(10)
        self.addCleanup(listener.close)
        result = queue.Queue()

        def serve():
            try:
                connection, address = listener.accept()
                with connection:
                    connection.settimeout(10)
                    result.put((True, handler(connection)))
            except BaseException as error:
                result.put((False, error))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        process = subprocess.Popen([
            sys.executable, str(ROOT / 'scripts/portable-export-relay.py'),
            '--host', '127.0.0.1', '--port', str(listener.getsockname()[1]),
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'SSH_ORIGINAL_COMMAND': original_command})

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
            thread.join(timeout=1)

        self.addCleanup(cleanup)
        return process, result

    def server_result(self, result):
        ok, value = result.get(timeout=10)
        if not ok:
            raise value
        return value

    @staticmethod
    def receive_all(connection):
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                return b''.join(chunks)
            chunks.append(chunk)

    def test_duplex_stream_uses_fixed_cli_target_and_ignores_original_command(self):
        data = b'\x00binary SSH input\xff' * 100

        def handler(connection):
            connection.sendall(b'initial SSH banner\n')
            received = self.receive_all(connection)
            connection.sendall(received[::-1])
            return received

        process, result = self.start(handler, '--host attacker.invalid; /bin/sh -c anything')
        output, errors = process.communicate(data, timeout=10)
        self.assertEqual(process.returncode, 0, errors.decode())
        self.assertEqual(output, b'initial SSH banner\n' + data[::-1])
        self.assertEqual(self.server_result(result), data)

    def test_stdin_half_close_retains_the_servers_final_response(self):
        def handler(connection):
            received = self.receive_all(connection)
            connection.sendall(b'response after input EOF')
            return received

        process, result = self.start(handler)
        output, errors = process.communicate(b'request', timeout=10)
        self.assertEqual(process.returncode, 0, errors.decode())
        self.assertEqual(output, b'response after input EOF')
        self.assertEqual(self.server_result(result), b'request')

    def test_server_half_close_reaches_stdout_before_client_stdin_closes(self):
        def handler(connection):
            connection.sendall(b'output finished')
            connection.shutdown(socket.SHUT_WR)
            return self.receive_all(connection)

        process, result = self.start(handler)
        output = bytearray()
        deadline = time.monotonic() + 10
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                self.assertLess(time.monotonic(), deadline, 'Server EOF was not propagated')
                if not selector.select(.1):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
        self.assertEqual(bytes(output), b'output finished')
        self.assertIsNone(process.poll())
        process.stdin.write(b'input after server EOF')
        process.stdin.close()
        self.assertEqual(process.wait(timeout=10), 0, process.stderr.read().decode())
        self.assertEqual(self.server_result(result), b'input after server EOF')

    def test_large_bidirectional_streams_handle_backpressure_without_data_loss(self):
        request = os.urandom(3 * RELAY.BUFFER_LIMIT + 123)
        response = os.urandom(3 * RELAY.BUFFER_LIMIT + 321)

        def handler(connection):
            connection.sendall(response)
            return self.receive_all(connection)

        process, result = self.start(handler)
        output, errors = process.communicate(request, timeout=15)
        self.assertEqual(process.returncode, 0, errors.decode())
        self.assertEqual(output, response)
        self.assertEqual(self.server_result(result), request)

    def test_bbr_unavailable_keeps_default_congestion_control(self):
        connection = mock.Mock()
        connection.setsockopt.side_effect = OSError('Not supported')
        with mock.patch.object(RELAY.socket, 'TCP_CONGESTION', 13, create=True):
            self.assertFalse(RELAY.prefer_bbr(connection))
            connection.setsockopt.assert_called_once_with(socket.IPPROTO_TCP, 13, b'bbr')


if __name__ == '__main__':
    unittest.main()
