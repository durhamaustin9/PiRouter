#!/usr/bin/python3
"""Copy one immutable Time Machine snapshot using durable, verified chunks.

Local SQLite state records directory enumeration and acknowledged receiver work.
The source identity uses Time Machine's mount UUID and the pinned snapshot name;
outside a Time Machine mount it uses the resolved source path (for fixtures).
Only the receiver's durable acknowledgement advances a file's saved offset.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request


CHUNK_SIZE = 8 * 1024 * 1024
SCAN_BATCH_SIZE = 128
BATCH_MAX_ENTRIES = 64
# Medium documents can complete in one durable request instead of four legacy
# round trips. Base64 for the entire payload still fits inside the frame budget.
BATCH_MAX_FILE_BYTES = 512 * 1024
BATCH_MAX_PAYLOAD_BYTES = 512 * 1024
BATCH_MAX_JSON_BYTES = 900 * 1024
HEARTBEAT_INTERVAL = 30.0
IO_TIMEOUT = 180.0
CLOSE_TIMEOUT = 3.0
PRIORITY_VERSION = 'personal-first-v3'
EXCLUDED_ROOT_FILES = {'.com.apple.timemachine.checkpoint', '.special_paths.plist'}
EXCLUDED_DATA_PATHS = (
    'private/var',
    'private/etc/cups/certs',
    'Library/Application Support/Apple/ParentalControls/Users',
    'Library/Preferences/com.express.vpn/api_client',
    'Library/Preferences/com.express.vpn/csdk',
)


class ExportError(Exception):
    pass


class PauseExport(ExportError):
    pass


class SessionBudgetEnded(PauseExport):
    pass


class AlreadyRunning(ExportError):
    pass


def excluded(relative):
    first = relative.split('/', 1)[0]
    if first in EXCLUDED_ROOT_FILES or first.startswith('.pirouter'):
        return True
    for suffix in EXCLUDED_DATA_PATHS:
        prefix = 'Macintosh HD - Data/' + suffix
        if relative == prefix or relative.startswith(prefix + '/'):
            return True
    return False


def priority(relative):
    if not relative:
        return -1
    main = 'Macintosh HD - Data'
    parts = relative.split('/')
    if parts[0] != main:
        return 5
    if len(parts) == 1:
        # Match this directory's ordinary direct files so a large flat volume
        # can still stream a batch before enumeration finishes. Its Users tree
        # receives a higher priority as soon as it is discovered.
        return 4
    if parts[1] != 'Users':
        return 4
    # Discover every user's home and its cloud-data ancestors before descending
    # into alphabetically earlier cache trees. No username is special-cased.
    if len(parts) <= 3:
        return 0
    home = tuple(parts[3:])
    personal = {'Desktop', 'Documents', 'Pictures', 'Movies', 'Music', 'Downloads',
                'Mobile Documents', 'MobileDocuments', 'CloudStorage'}
    if home[0] in personal:
        return 1
    if home == ('Library',):
        return 0
    if home[0] == 'Library' and len(home) > 1 and home[1] in {'Mobile Documents', 'MobileDocuments', 'CloudStorage'}:
        return 1
    caches = (('.cache',), ('.npm',), ('.pnpm-store',), ('.yarn', 'cache'),
              ('.bun', 'install', 'cache'), ('.gradle', 'caches'),
              ('.m2', 'repository'), ('.cargo', 'registry'), ('.cargo', 'git'),
              ('.local', 'cache'), ('.local', 'share', 'cache'),
              ('.local', 'share', 'pnpm', 'store'))
    if any(home[:len(prefix)] == prefix for prefix in caches):
        return 10
    if home[0] == 'Library' and any(part in {'Caches', 'Cache', 'GPUCache', 'Code Cache', 'DerivedData'} for part in home[1:]):
        return 10
    return 2


def source_identity(source, snapshot):
    resolved = str(Path(source).resolve())
    match = re.search(r'/\.timemachine/([0-9a-fA-F-]{36})(?:/|$)', resolved)
    stable = match.group(1).lower() if match else resolved
    return hashlib.sha256(('pirouter-portable-v1\0' + stable + '\0' + snapshot).encode()).hexdigest()


def metadata(relative, info, link=None):
    if stat.S_ISREG(info.st_mode):
        kind = 'file'
    elif stat.S_ISDIR(info.st_mode):
        kind = 'dir'
    elif stat.S_ISLNK(info.st_mode):
        kind = 'symlink'
    else:
        raise ExportError('Unsupported backup file type: ' + relative)
    values = [relative, kind, info.st_size, info.st_mtime_ns,
              stat.S_IMODE(info.st_mode), link]
    identity = hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return dict(path=relative, kind=kind, identity=identity, size=info.st_size,
                mode=stat.S_IMODE(info.st_mode), mtime_ns=info.st_mtime_ns, link=link)


class SSHReceiver:
    def __init__(self, args, identity):
        body = Path(args.receiver_script).read_text()
        if body.lstrip().startswith('<?php'):
            raise ExportError('Receiver script must be PHP code without an opening tag')
        command = 'php -r {} -- {} {} {}'.format(
            shlex.quote(body), shlex.quote(args.root), shlex.quote(args.snapshot),
            shlex.quote(identity))
        self.errors = tempfile.TemporaryFile()
        ssh_args = [
            '/usr/bin/ssh', '-i', args.ssh_key, '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=30',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
        ]
        proxy = getattr(args, 'proxy_command', '')
        if proxy:
            ssh_args.extend(['-o', 'ProxyCommand=' + proxy])
        ssh_args.extend([args.host, command])
        self.process = subprocess.Popen(
            ssh_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            bufsize=0, start_new_session=True)
        self._initialize_transport(owns_session=True)

    def _initialize_transport(self, owns_session=False):
        # Test transports may supply their own Popen without a separate session.
        # Never signal that process's group unless this transport created it.
        try:
            self._owns_session = owns_session and os.getpgid(self.process.pid) == self.process.pid
        except ProcessLookupError:
            self._owns_session = False
        self._rpc_lock = threading.RLock()
        self._read_buffer = bytearray()
        self._healthy = True
        self._raw_pending = False
        self._interrupted = False
        self._finished = False
        self._close_supported = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._closed = False
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)

    def _connection_error(self, detail=None):
        self._healthy = False
        if detail is None:
            self.errors.seek(0, os.SEEK_END)
            self.errors.seek(max(0, self.errors.tell() - 1500))
            detail = self.errors.read().decode('utf-8', 'replace').strip()
        return PauseExport('Unraid connection ended' + (': ' + detail if detail else ''))

    def _wait_pipe(self, descriptor, writing=False, deadline=None):
        while True:
            if self._interrupted:
                raise self._connection_error('The Mac exporter was interrupted')
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise self._connection_error('The connection stopped making progress')
            delay = 0.25 if remaining is None else min(0.25, remaining)
            readable, writable, _ = select.select(
                [] if writing else [descriptor], [descriptor] if writing else [], [], delay)
            if readable or writable:
                return

    def _write_bytes(self, data, deadline=None):
        descriptor = self.process.stdin.fileno()
        pending = memoryview(data)
        progress_deadline = time.monotonic() + IO_TIMEOUT
        while pending:
            self._wait_pipe(descriptor, writing=True, deadline=min(progress_deadline, deadline)
                            if deadline is not None else progress_deadline)
            try:
                length = os.write(descriptor, pending)
            except BlockingIOError:
                continue
            except OSError as error:
                raise self._connection_error(str(error))
            if not length:
                raise self._connection_error()
            pending = pending[length:]
            progress_deadline = time.monotonic() + IO_TIMEOUT

    def _receive(self, deadline=None):
        # Keep partial frames across nonblocking reads. A live receiver may be
        # doing a long fsync, so normal replies have no wall-clock cutoff; signal
        # interruption and the bounded close handshake remain responsive.
        descriptor = self.process.stdout.fileno()
        while True:
            newline = self._read_buffer.find(b'\n')
            if newline >= 0:
                if newline >= 1024 * 1024:
                    raise self._connection_error('Unraid returned an oversized protocol frame')
                line = bytes(self._read_buffer[:newline])
                del self._read_buffer[:newline + 1]
                break
            if len(self._read_buffer) >= 1024 * 1024:
                raise self._connection_error('Unraid returned an oversized protocol frame')
            self._wait_pipe(descriptor, deadline=deadline)
            try:
                data = os.read(descriptor, 65536)
            except BlockingIOError:
                continue
            except OSError as error:
                raise self._connection_error(str(error))
            if not data:
                raise self._connection_error()
            self._read_buffer.extend(data)
        try:
            reply = json.loads(line)
        except (ValueError, UnicodeError):
            raise self._connection_error('Invalid reply from the Unraid receiver')
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            raise self._connection_error(str(reply.get('error', 'Unraid rejected the export'))
                                         if isinstance(reply, dict) else 'Invalid Unraid reply')
        return reply

    def _exchange(self, message, deadline=None):
        if self._raw_pending:
            raise self._connection_error('A raw file payload is still pending')
        self._write_bytes(json.dumps(message, ensure_ascii=True).encode() + b'\n', deadline)
        reply = self._receive(deadline)
        self._raw_pending = message.get('op') == 'chunk' and reply.get('needData') is True
        if reply.get('published') is True:
            self._finished = True
        return reply

    def start(self):
        with self._rpc_lock:
            ready = self._receive()
            self._finished = ready.get('published') is True
            lifecycle = ready.get('lifecycle')
            if isinstance(lifecycle, dict) and type(lifecycle.get('version')) is int and lifecycle['version'] == 1:
                self._close_supported = lifecycle.get('close') is True
                if lifecycle.get('heartbeat') is True and not self._finished:
                    self._start_heartbeats()
            return ready

    def _start_heartbeats(self):
        def heartbeats():
            while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL):
                if not self._rpc_lock.acquire(timeout=0.1):
                    continue
                try:
                    if self._heartbeat_stop.is_set() or not self._healthy or self._finished:
                        return
                    if self._raw_pending:
                        continue
                    reply = self._exchange(dict(op='heartbeat'))
                    if reply.get('alive') is not True:
                        raise self._connection_error('Unraid did not acknowledge its heartbeat')
                except (ExportError, OSError, ValueError):
                    self._healthy = False
                    return
                finally:
                    self._rpc_lock.release()
        self._heartbeat_thread = threading.Thread(target=heartbeats, daemon=True)
        self._heartbeat_thread.start()

    def request(self, message):
        with self._rpc_lock:
            if not self._healthy:
                raise self._connection_error()
            return self._exchange(message)

    def write_chunk(self, data):
        with self._rpc_lock:
            if not self._healthy or not self._raw_pending:
                raise self._connection_error('No raw file payload was requested')
            self._write_bytes(data)
            reply = self._receive()
            self._raw_pending = False
            return reply

    def _signal_process(self, signum):
        try:
            if self._owns_session:
                os.killpg(self.process.pid, signum)
            elif self.process.poll() is None:
                self.process.send_signal(signum)
        except ProcessLookupError:
            pass
        except PermissionError:
            # A test transport or an already-reaped process can expose a group
            # we do not own. Cleanup must never mask the saved transfer result.
            self._owns_session = False
            try:
                if self.process.poll() is None:
                    self.process.send_signal(signum)
            except (ProcessLookupError, PermissionError):
                pass

    def interrupt(self):
        self._interrupted = True
        self._healthy = False
        self._heartbeat_stop.set()
        self._signal_process(signal.SIGTERM)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._heartbeat_stop.set()
        deadline = time.monotonic() + CLOSE_TIMEOUT
        locked = self._rpc_lock.acquire(timeout=max(0, deadline - time.monotonic()))
        try:
            if locked and self._healthy and not self._raw_pending and not self._finished and \
                    self.process.poll() is None and self._close_supported:
                try:
                    reply = self._exchange(dict(op='close'), deadline)
                    if reply.get('closed') is not True:
                        self._healthy = False
                except (ExportError, OSError, ValueError):
                    self._healthy = False
        finally:
            if locked:
                self._rpc_lock.release()
        # stdin is unbuffered in production. A subclass may provide a buffered
        # handle, so terminate first on every unsafe/unfinished protocol path.
        if not locked or not self._healthy or self._raw_pending:
            self._interrupted = True
            self._signal_process(signal.SIGTERM)
        else:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            self._interrupted = True
            self._signal_process(signal.SIGTERM)
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._signal_process(signal.SIGKILL)
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # Shutdown must remain bounded even if the operating system
                    # denies signalling. The receiver also has its input lease.
                    pass
        # The outer SSH can exit while a ProxyCommand descendant remains. Its
        # private session contains no unrelated SSH jobs and must be retired too.
        self._signal_process(signal.SIGTERM)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=0.5)
        if self._owns_session:
            self._signal_process(signal.SIGKILL)
        for handle in (self.process.stdin, self.process.stdout, self.errors):
            if not handle.closed:
                try:
                    handle.close()
                except (BrokenPipeError, OSError):
                    pass


class Progress:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.phase = 'Checking the pinned backup snapshot'
        self.bytes_copied = 0
        self.files_copied = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.urls = [value.strip().rstrip('/') for value in
                     os.environ.get('PIROUTER_URLS', '').split(',') if value.strip()]
        self.token = os.environ.get('PIROUTER_TOKEN', '')
        self.request_id = os.environ.get('PIROUTER_REQUEST_ID', '')
        self.thread = None

    def set(self, phase=None, bytes_copied=None, files_copied=None):
        with self.lock:
            if phase is not None:
                self.phase = phase
            if bytes_copied is not None:
                self.bytes_copied = bytes_copied
            if files_copied is not None:
                self.files_copied = files_copied

    def values(self):
        with self.lock:
            return dict(snapshot=self.snapshot, phase=self.phase,
                        bytesCopied=self.bytes_copied, filesCopied=self.files_copied)

    def report(self, state='running', detail=None, pause_reason=None):
        values = self.values()
        message = values['phase'][:240]
        scheduled = state == 'paused' and pause_reason == 'session-budget'
        if scheduled:
            message = 'Continuing automatically; saved work is retained'
        elif state == 'paused':
            message = 'Export paused; uploaded work is retained and will resume automatically'
            if detail:
                message = ('Export paused; saved work is retained. ' + str(detail))[:240]
        elif state == 'success':
            message = 'Browsable snapshot {} is ready on Unraid'.format(self.snapshot)
        payload = dict(requestId=self.request_id, state=state, message=message,
                       activeSnapshotName=self.snapshot,
                       bytesCopied=values['bytesCopied'], filesCopied=values['filesCopied'],
                       verified=state == 'success')
        if state == 'success':
            payload['lastSnapshotName'] = self.snapshot
        if scheduled:
            payload['pauseReason'] = 'session-budget'
        elif detail:
            payload['errorDetail'] = str(detail)[-4000:]
        if not self.token or not self.request_id:
            return
        encoded = json.dumps(payload).encode()
        for url in self.urls:
            request = urllib.request.Request(url + '/api/portable-export', data=encoded,
                                             method='PATCH', headers={
                                                 'Authorization': 'Bearer ' + self.token,
                                                 'Content-Type': 'application/json',
                                             })
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    response.read(4096)
                return
            except (OSError, ValueError, urllib.error.URLError):
                continue

    def start(self):
        def heartbeats():
            while not self.stopped.wait(15):
                self.report()
                print(json.dumps(dict(state='running', **self.values())), flush=True)
        self.thread = threading.Thread(target=heartbeats, daemon=True)
        self.thread.start()
        self.report()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)


class ExportEngine:
    def __init__(self, args, transport_factory=None):
        self.args = args
        self.source = Path(args.source)
        self.identity = source_identity(self.source, args.snapshot)
        self.transport_factory = transport_factory or (lambda: SSHReceiver(args, self.identity))
        self.connection = None
        self.db = None
        self.lock_handle = None
        self.progress = Progress(args.snapshot)
        self.stop_requested = False
        self.deadline = time.monotonic() + args.session_seconds if args.session_seconds else None
        self.chunks_sent = 0
        self.next_payload_time = time.monotonic()
        self.scanners = {}
        self.batch_limits = None

    def check_session(self):
        if self.stop_requested:
            raise PauseExport('The Mac exporter was interrupted')
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SessionBudgetEnded('Continuing automatically; saved work is retained')
        if self.args.max_chunks is not None and self.chunks_sent >= self.args.max_chunks:
            raise PauseExport('This export session reached its chunk budget')

    def interrupt(self):
        self.stop_requested = True
        if self.connection:
            self.connection.interrupt()

    def _meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM job WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO job(key,value) VALUES(?,?)', (key, str(value)))

    def _open_state(self):
        state = Path(self.args.state_dir)
        state.mkdir(parents=True, exist_ok=True)
        self.lock_handle = (state / (self.args.snapshot + '.lock')).open('a+')
        try:
            fcntl.flock(self.lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning('This snapshot already has an active exporter')
        self.db = sqlite3.connect(str(state / (self.args.snapshot + '.sqlite3')))
        self.db.row_factory = sqlite3.Row
        self.db.create_function('portable_priority', 1, priority)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS job(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS volumes(
                name TEXT PRIMARY KEY,regular_files INTEGER NOT NULL DEFAULT 0,
                probed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS directories(
                path TEXT PRIMARY KEY,priority INTEGER NOT NULL,
                scanned INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS entries(
                path TEXT PRIMARY KEY,kind TEXT NOT NULL,identity TEXT NOT NULL,
                size INTEGER NOT NULL,mode INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,
                link TEXT,priority INTEGER NOT NULL,done INTEGER NOT NULL DEFAULT 0,
                offset INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS pending_entries ON entries(done,priority,path);
            CREATE INDEX IF NOT EXISTS pending_directories ON directories(scanned,priority,path);
        ''')
        self.progress.set(bytes_copied=int(self._meta('bytes_copied', '0')),
                          files_copied=int(self._meta('files_copied', '0')))
        with self.db:
            old_identity = self._meta('source_id')
            if old_identity and old_identity != self.identity:
                raise ExportError('The pinned snapshot belongs to a different backup source')
            self._set_meta('source_id', self.identity)
            self._set_meta('snapshot', self.args.snapshot)
            if self._meta('priority_version') != PRIORITY_VERSION:
                # Older queues gave all home paths the same priority, so merely
                # changing newly discovered rows would leave cached work first.
                # Only ordering changes; completed work and offsets are kept.
                self.db.execute('UPDATE entries SET priority=portable_priority(path)')
                self.db.execute('UPDATE directories SET priority=portable_priority(path)')
                self._set_meta('priority_version', PRIORITY_VERSION)

    def _check_volumes(self):
        mounted_name = self.source.name
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{6}\.backup', mounted_name) and \
                mounted_name[:-7] != self.args.snapshot:
            raise ExportError('The mounted snapshot does not match the pinned export job')
        if not self.source.is_dir():
            raise ExportError('The pinned Time Machine snapshot is not mounted')
        discovered = []
        with os.scandir(self.source) as entries:
            for entry in entries:
                if entry.name.startswith('.'):
                    continue
                if entry.is_symlink():
                    raise ExportError('Backup volume is a symlink: ' + entry.name)
                if entry.is_dir(follow_symlinks=False):
                    discovered.append(entry.name)
        if not discovered:
            raise ExportError('No mounted backup volumes were found')
        stored = [row[0] for row in self.db.execute('SELECT name FROM volumes')]
        if stored and set(stored) != set(discovered):
            raise ExportError('The pinned backup has missing or different volumes')
        with self.db:
            for name in discovered:
                self.db.execute('INSERT OR IGNORE INTO volumes(name) VALUES(?)', (name,))
            self.db.execute('INSERT OR IGNORE INTO directories(path,priority) VALUES(?,?)', ('', -1))

    def _probe_volume(self, name):
        queue = [name]
        while queue:
            self.check_session()
            relative = queue.pop()
            children = []
            with os.scandir(self.source / relative) as entries:
                for entry in entries:
                    self.check_session()
                    child = relative + '/' + entry.name
                    if excluded(child):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        descriptor = os.open(str(self.source / child), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                        try:
                            os.read(descriptor, 1)
                        finally:
                            os.close(descriptor)
                        with self.db:
                            self.db.execute('UPDATE volumes SET probed=1 WHERE name=?', (name,))
                        return
                    if stat.S_ISDIR(info.st_mode):
                        children.append(child)
            queue.extend(sorted(children, reverse=True))
        raise ExportError('Backup volume is empty or unreadable: ' + name)

    def _scan_directory(self, relative, until=None):
        self.check_session()
        directory = self.source / relative
        if relative not in self.scanners:
            before = directory.lstat()
            if not stat.S_ISDIR(before.st_mode):
                raise ExportError('A backup directory changed or became a symlink: ' + relative)
            if relative:
                entry = self.db.execute('SELECT * FROM entries WHERE path=?', (relative,)).fetchone()
                if entry:
                    self._check_entry(entry, before)
            self.scanners[relative] = (os.scandir(directory), before)
        iterator, before = self.scanners[relative]
        self.progress.set(phase='Scanning backed-up folders: ' + (relative or self.args.snapshot)[-180:])
        records = []
        complete = False
        root_volumes = {row[0] for row in self.db.execute('SELECT name FROM volumes')} if not relative else None
        while len(records) < SCAN_BATCH_SIZE:
            self.check_session()
            if until is not None and time.monotonic() >= until:
                break
            try:
                entry = next(iterator)
            except StopIteration:
                complete = True
                break
            if root_volumes is not None and entry.name not in root_volumes:
                continue
            child = relative + '/' + entry.name if relative else entry.name
            if excluded(child) or self.db.execute('SELECT 1 FROM entries WHERE path=?', (child,)).fetchone():
                continue
            info = entry.stat(follow_symlinks=False)
            link = os.readlink(self.source / child) if stat.S_ISLNK(info.st_mode) else None
            records.append(metadata(child, info, link))
        after = directory.lstat()
        if (before.st_mtime_ns, before.st_mode, before.st_ino) != (after.st_mtime_ns, after.st_mode, after.st_ino):
            raise ExportError('The backup directory changed while scanning: ' + relative)
        with self.db:
            for record in records:
                cursor = self.db.execute('''
                    INSERT OR IGNORE INTO entries(path,kind,identity,size,mode,mtime_ns,link,priority)
                    VALUES(:path,:kind,:identity,:size,:mode,:mtime_ns,:link,:priority)
                ''', dict(record, priority=priority(record['path'])))
                if cursor.rowcount and record['kind'] == 'file':
                    self.db.execute('UPDATE volumes SET regular_files=regular_files+1 WHERE name=?',
                                    (record['path'].split('/', 1)[0],))
                if record['kind'] == 'dir':
                    self.db.execute('INSERT OR IGNORE INTO directories(path,priority) VALUES(?,?)',
                                    (record['path'], priority(record['path'])))
            if complete:
                self.db.execute('UPDATE directories SET scanned=1 WHERE path=?', (relative,))
        if complete:
            iterator.close()
            del self.scanners[relative]

    def _remember_progress(self):
        values = self.progress.values()
        self._set_meta('bytes_copied', values['bytesCopied'])
        self._set_meta('files_copied', values['filesCopied'])

    def _mark_done(self, entry, counted=False):
        if counted and entry['kind'] == 'file':
            values = self.progress.values()
            self.progress.set(files_copied=values['filesCopied'] + 1)
        with self.db:
            self.db.execute('UPDATE entries SET done=1,offset=? WHERE path=?',
                            (entry['size'] if entry['kind'] == 'file' else 0, entry['path']))
            self._remember_progress()

    def _check_entry(self, entry, info=None):
        path = self.source / entry['path']
        info = info or path.lstat()
        link = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
        if metadata(entry['path'], info, link)['identity'] != entry['identity']:
            raise ExportError('A file in the pinned snapshot changed: ' + entry['path'])

    def _pace(self, length):
        # Reading, hashing, network transfer and the receiver's durable write
        # already consume time within the configured bandwidth allowance. Only
        # wait for the remainder, rather than adding a full payload-sized sleep
        # on top of that work. Clamp past deadlines to now so idle/scanning time
        # cannot build up credit for a succession of unbounded payload bursts.
        target = max(time.monotonic(), self.next_payload_time +
                     length / (self.args.bandwidth_kb * 1024))
        self.next_payload_time = target
        while True:
            self.check_session()
            remaining = target - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))

    def _configure_batch(self, ready):
        capability = ready.get('batch')
        if not isinstance(capability, dict) or type(capability.get('version')) is not int or \
                capability['version'] != 1:
            return
        limits = {}
        for name, maximum in (('maxEntries', BATCH_MAX_ENTRIES),
                              ('maxFileBytes', BATCH_MAX_FILE_BYTES),
                              ('maxPayloadBytes', BATCH_MAX_PAYLOAD_BYTES)):
            value = capability.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return
            limits[name] = min(value, maximum)
        self.batch_limits = limits

    @staticmethod
    def _entry_message(entry):
        message = dict(path=entry['path'], kind=entry['kind'],
                       identity=entry['identity'], size=entry['size'], mode=entry['mode'],
                       mtime=entry['mtime_ns'] // 1_000_000_000)
        if entry['kind'] == 'symlink':
            message['link'] = entry['link']
        return message

    def _pending_entries(self, limit):
        directory = self.db.execute('''SELECT path,priority FROM directories WHERE scanned=0
            ORDER BY priority,path LIMIT 1''').fetchone()
        if directory:
            pending = self.db.execute('''SELECT * FROM entries WHERE done=0 AND priority<=?
                ORDER BY priority,path LIMIT ?''', (directory['priority'], limit)).fetchall()
        else:
            pending = self.db.execute('''SELECT * FROM entries WHERE done=0
                ORDER BY priority,path LIMIT ?''', (limit,)).fetchall()
        return pending, directory

    def _batch_prefix(self, pending):
        """Select an ordered prefix without opening files or overtaking large work."""
        selected = []
        payload = chunks = 0
        frame_size = len(json.dumps(dict(op='batch', entries=[])).encode())
        for entry in pending:
            if len(selected) >= self.batch_limits['maxEntries']:
                return selected, True
            message = self._entry_message(entry)
            if entry['kind'] == 'file':
                size = entry['size']
                if entry['offset'] or size > min(self.batch_limits['maxFileBytes'],
                                                 self.batch_limits['maxPayloadBytes']):
                    return selected, True
                if payload + size > self.batch_limits['maxPayloadBytes']:
                    return selected, True
                if size and self.args.max_chunks is not None and \
                        self.chunks_sent + chunks >= self.args.max_chunks:
                    return selected, True
                # Account for canonical base64 without reading source data yet.
                message.update(data='', sha256='0' * 64)
                encoded_data_size = 4 * ((size + 2) // 3)
            else:
                size = encoded_data_size = 0
            addition = len(json.dumps(message, ensure_ascii=True).encode()) + encoded_data_size
            if frame_size + addition + (2 if selected else 0) > BATCH_MAX_JSON_BYTES:
                return selected, True
            frame_size += addition + (2 if selected else 0)
            selected.append(entry)
            payload += size
            chunks += bool(size)
        return selected, False

    def _fill_batch_queue(self):
        """Discover a small bounded lookahead, including several small folders."""
        until = time.monotonic() + 1.0
        for _ in range(32):
            self.check_session()
            pending, directory = self._pending_entries(self.batch_limits['maxEntries'] + 1)
            selected, full = self._batch_prefix(pending)
            if full or directory is None or time.monotonic() >= until:
                return selected, pending, directory
            self._scan_directory(directory['path'], until=until)
        pending, directory = self._pending_entries(self.batch_limits['maxEntries'] + 1)
        return self._batch_prefix(pending)[0], pending, directory

    def _checkpoint_batch(self, entries, counters):
        # Keep the row acknowledgements and absolute receiver counters in the
        # same transaction. A lost ACK or failed commit leaves this batch pending.
        with self.db:
            self.db.executemany('UPDATE entries SET done=1,offset=? WHERE path=?',
                                [(entry['size'] if entry['kind'] == 'file' else 0,
                                  entry['path']) for entry in entries])
            self._set_meta('bytes_copied', counters['bytesCopied'])
            self._set_meta('files_copied', counters['filesCopied'])
        self.progress.set(bytes_copied=counters['bytesCopied'], files_copied=counters['filesCopied'])

    def _copy_batch(self, entries):
        self.check_session()
        self.progress.set(phase='Preparing {} backed-up files and folders'.format(len(entries)))
        messages = []
        checked = []
        payload = chunks = 0
        for entry in entries:
            self.check_session()
            path = self.source / entry['path']
            before = path.lstat()
            self._check_entry(entry, before)
            message = self._entry_message(entry)
            if entry['kind'] == 'file':
                descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(descriptor, 'rb') as source_file:
                    opened = os.fstat(source_file.fileno())
                    self._check_entry(entry, opened)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise ExportError('A backup file was replaced before reading: ' + entry['path'])
                    data = source_file.read(entry['size'])
                    if len(data) != entry['size']:
                        raise ExportError('The backup file could not be read completely: ' + entry['path'])
                    self._check_entry(entry, os.fstat(source_file.fileno()))
                message.update(data=base64.b64encode(data).decode('ascii'),
                               sha256=hashlib.sha256(data).hexdigest())
                payload += len(data)
                chunks += bool(data)
            self._check_entry(entry)
            checked.append((entry, before.st_dev, before.st_ino))
            messages.append(message)
        message = dict(op='batch', entries=messages)
        if len(json.dumps(message, ensure_ascii=True).encode()) > BATCH_MAX_JSON_BYTES:
            raise ExportError('A file batch exceeds the safe protocol frame size')
        if payload:
            self._pace(payload)
        # Pacing can wait, so check every path again immediately before sending.
        for entry, device, inode in checked:
            self.check_session()
            current = (self.source / entry['path']).lstat()
            self._check_entry(entry, current)
            if (current.st_dev, current.st_ino) != (device, inode):
                raise ExportError('A backup entry was replaced while preparing a batch: ' + entry['path'])
        self.progress.set(phase='Uploading {} backed-up files and folders together'.format(len(entries)))
        reply = self.connection.request(message)
        if reply.get('batchComplete') is not True or reply.get('paths') != [entry['path'] for entry in entries]:
            raise ExportError('Unraid did not acknowledge the exact completed file batch')
        old = self.progress.values()
        minimum = dict(bytesCopied=payload,
                       filesCopied=sum(entry['kind'] == 'file' for entry in entries))
        for field in ('bytesCopied', 'filesCopied'):
            value = reply.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < max(old[field], minimum[field]):
                raise ExportError('Unraid returned invalid batch progress counters')
        self._checkpoint_batch(entries, reply)
        self.chunks_sent += chunks

    def _copy_entry(self, entry):
        self.check_session()
        self._check_entry(entry)
        self.progress.set(phase='Uploading backed-up files: ' + entry['path'][-180:])
        message = dict(op='begin', **self._entry_message(entry))
        reply = self.connection.request(message)
        if reply.get('complete') is True:
            self._mark_done(entry)
            return
        if entry['kind'] != 'file':
            raise ExportError('Unraid did not acknowledge a directory or symlink')
        offset = reply.get('offset')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > entry['size']:
            raise ExportError('Unraid returned an invalid saved file offset')
        if offset != entry['offset']:
            self._set_remote_progress(self.connection.request(dict(op='status')))
        with self.db:
            self.db.execute('UPDATE entries SET offset=? WHERE path=?', (offset, entry['path']))
        descriptor = os.open(str(self.source / entry['path']), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as source_file:
            self._check_entry(entry, os.fstat(source_file.fileno()))
            source_file.seek(offset)
            while offset < entry['size']:
                self.check_session()
                length = min(CHUNK_SIZE, entry['size'] - offset)
                data = source_file.read(length)
                if len(data) != length:
                    raise ExportError('The backup file could not be read completely: ' + entry['path'])
                self._check_entry(entry, os.fstat(source_file.fileno()))
                self._pace(length)
                acknowledgement = self.connection.request(dict(
                    op='chunk', path=entry['path'], offset=offset, length=length,
                    sha256=hashlib.sha256(data).hexdigest()))
                if acknowledgement.get('needData') is True:
                    acknowledgement = self.connection.write_chunk(data)
                elif acknowledgement.get('needData') is not False:
                    raise ExportError('Unraid did not acknowledge the chunk offer')
                new_offset = acknowledgement.get('offset')
                if isinstance(new_offset, bool) or not isinstance(new_offset, int) or new_offset != offset + length:
                    raise ExportError('Unraid acknowledged an unexpected file offset')
                values = self.progress.values()
                self.progress.set(bytes_copied=values['bytesCopied'] + length)
                offset = new_offset
                with self.db:
                    self.db.execute('UPDATE entries SET offset=? WHERE path=?', (offset, entry['path']))
                    self._remember_progress()
                self.chunks_sent += 1
            self._check_entry(entry, os.fstat(source_file.fileno()))
        self.connection.request(dict(op='finish', path=entry['path']))
        self._mark_done(entry, counted=True)

    def _set_remote_progress(self, reply):
        for field in ('bytesCopied', 'filesCopied'):
            value = reply.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExportError('Unraid returned invalid progress counters')
        self.progress.set(bytes_copied=reply['bytesCopied'], files_copied=reply['filesCopied'])
        with self.db:
            self._remember_progress()

    def _publish(self):
        self.check_session()
        volumes = [dict(name=row['name'], regularFiles=row['regular_files'])
                   for row in self.db.execute('SELECT name,regular_files FROM volumes ORDER BY name')]
        if not volumes or any(volume['regularFiles'] < 1 for volume in volumes):
            raise ExportError('An empty backup volume cannot be published')
        expected_files = self.db.execute("SELECT count(*) FROM entries WHERE kind='file'").fetchone()[0]
        expected_entries = self.db.execute('SELECT count(*) FROM entries').fetchone()[0]
        if self.db.execute('SELECT count(*) FROM entries WHERE done=0').fetchone()[0] or \
                self.db.execute('SELECT count(*) FROM directories WHERE scanned=0').fetchone()[0]:
            raise ExportError('Unfinished backup work cannot be published')
        self.progress.set(phase='Publishing the verified browsable snapshot on Unraid')
        message = dict(op='publish', volumes=volumes, expectedFiles=expected_files,
                       expectedEntries=expected_entries)
        while True:
            self.check_session()
            reply = self.connection.request(message)
            self._set_remote_progress(reply)
            if reply.get('published') is True:
                break
            if reply.get('finalizing') is not True:
                raise ExportError('Unraid did not confirm the completed snapshot')
            checked = reply.get('checkedEntries')
            total = reply.get('totalEntries')
            self.progress.set(phase='Verifying exported entries on Unraid: {} / {}'.format(checked, total))
        with self.db:
            self._set_meta('published', 'true')

    def run(self):
        try:
            self._open_state()
            self.check_session()
            self._check_volumes()
            self.connection = self.transport_factory()
            self.check_session()
            ready = self.connection.start()
            self._configure_batch(ready)
            old_bytes = int(self._meta('bytes_copied', '0'))
            old_files = int(self._meta('files_copied', '0'))
            self._set_remote_progress(ready)
            if ready.get('published') is True:
                self.progress.report('success')
                print(json.dumps(dict(state='success', **self.progress.values())), flush=True)
                return 0
            if ready['bytesCopied'] < old_bytes or ready['filesCopied'] < old_files:
                with self.db:
                    self.db.execute('UPDATE entries SET done=0,offset=0')
            self.progress.start()
            for row in self.db.execute('SELECT name FROM volumes WHERE probed=0 ORDER BY name').fetchall():
                self.progress.set(phase='Checking backed-up volume: ' + row['name'])
                self._probe_volume(row['name'])
            while True:
                self.check_session()
                if self.batch_limits:
                    batch, pending, directory = self._fill_batch_queue()
                    if batch:
                        self._copy_batch(batch)
                    elif pending:
                        self._copy_entry(pending[0])
                    elif directory is None:
                        break
                    continue
                directory = self.db.execute('''SELECT path FROM directories WHERE scanned=0
                    ORDER BY priority,path LIMIT 1''').fetchone()
                if directory:
                    self._scan_directory(directory['path'])
                next_priority = self.db.execute('''SELECT priority FROM directories WHERE scanned=0
                    ORDER BY priority,path LIMIT 1''').fetchone()
                # Queued lower-priority files must not jump ahead of personal
                # data still being discovered. Equality keeps flat directories
                # streaming batches while their enumeration remains unfinished.
                if next_priority:
                    pending = self.db.execute('''SELECT * FROM entries WHERE done=0 AND priority<=?
                        ORDER BY priority,path LIMIT 32''', (next_priority[0],)).fetchall()
                else:
                    pending = self.db.execute('''SELECT * FROM entries WHERE done=0
                        ORDER BY priority,path LIMIT 32''').fetchall()
                for entry in pending:
                    self._copy_entry(entry)
                if directory is None and not pending:
                    break
            self._publish()
            self.progress.stop()
            self.progress.report('success')
            print(json.dumps(dict(state='success', **self.progress.values())), flush=True)
            return 0
        except AlreadyRunning:
            print(json.dumps(dict(state='already-running', snapshot=self.args.snapshot)), flush=True)
            return 0
        except SessionBudgetEnded as error:
            self.progress.stop()
            self.progress.report('paused', pause_reason='session-budget')
            print(json.dumps(dict(state='paused', pauseReason='session-budget', detail=str(error),
                                  **self.progress.values())), flush=True)
            return 76
        except (ExportError, OSError, sqlite3.Error) as error:
            self.progress.stop()
            self.progress.report('paused', str(error))
            print(json.dumps(dict(state='paused', detail=str(error), **self.progress.values())), flush=True)
            return 75
        except Exception as error:
            self.progress.stop()
            detail = 'Unexpected exporter failure: {}: {}'.format(type(error).__name__, error)
            self.progress.report('paused', detail)
            print(json.dumps(dict(state='paused', detail=detail, **self.progress.values())), flush=True)
            traceback.print_exc(file=sys.stderr)
            return 75
        finally:
            self.progress.stop()
            for iterator, before in self.scanners.values():
                iterator.close()
            if self.connection:
                self.connection.close()
            if self.db:
                self.db.close()
            if self.lock_handle:
                self.lock_handle.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'snapshot', 'state-dir', 'host', 'root', 'ssh-key', 'receiver-script'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--bandwidth-kb', type=int, default=40000)
    parser.add_argument('--proxy-command', default='',
                        help='Optional SSH ProxyCommand for the export connection')
    parser.add_argument('--session-seconds', type=float, default=600)
    parser.add_argument('--max-chunks', type=int, default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{6}', args.snapshot):
        parser.error('snapshot must be a completed Time Machine snapshot timestamp')
    if args.bandwidth_kb <= 0 or not math.isfinite(args.session_seconds) or args.session_seconds < 0 or \
            (args.max_chunks is not None and args.max_chunks < 0):
        parser.error('bandwidth must be positive and session budgets cannot be negative')
    return args


def main(argv=None):
    engine = ExportEngine(parse_args(argv))
    def interrupted(signum, frame):
        del signum, frame
        engine.interrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    return engine.run()


if __name__ == '__main__':
    sys.exit(main())
