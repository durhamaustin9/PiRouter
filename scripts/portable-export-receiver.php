declare(strict_types=1);

// This deliberately has no opening PHP tag: the SSH client runs it with php -r.
// Every acknowledged byte has been hashed, fsynced, read back, and committed to
// SQLite. The lifetime of this process is also the lifetime of the export lock.
ini_set('display_errors', 'stderr');
error_reporting(E_ALL);
set_error_handler(static function (int $severity, string $message, string $file, int $line): never {
    throw new ErrorException($message, 0, $severity, $file, $line);
});

const PROTOCOL_IDLE_SECONDS = 180.0;

// SSH servers may retain a pipe writer after a sleeping client disappears.
// Bound only waits for connection bytes, never local verification or disk I/O.
final class PeerDisconnected extends RuntimeException {}

final class ProtocolIO {
    private string $inputBuffer = '';
    public function __construct(private $input, private $output, private float $idleSeconds) {
        if (!stream_set_blocking($input, false) || !stream_set_blocking($output, false)
            || stream_set_write_buffer($output, 0) !== 0) fail('Could not configure bounded connection I/O');
    }
    private function now(): float { return hrtime(true) / 1000000000; }
    private function wait(bool $writing, float $deadline): void {
        while (true) {
            $remaining = $deadline - $this->now();
            if ($remaining <= 0) throw new PeerDisconnected('The export connection stopped making progress');
            $seconds = (int)$remaining;
            $microseconds = (int)(($remaining - $seconds) * 1000000);
            $read = $writing ? [] : [$this->input];
            $write = $writing ? [$this->output] : [];
            $except = [];
            try { $ready = stream_select($read, $write, $except, $seconds, $microseconds); }
            catch (Throwable $error) { throw new PeerDisconnected('Could not wait for the export connection', 0, $error); }
            if ($ready === false) throw new PeerDisconnected('Could not wait for the export connection');
            if ($ready > 0) return;
        }
    }
    private function receive(float &$deadline): bool {
        while (true) {
            try { $piece = fread($this->input, 65536); }
            catch (Throwable $error) { throw new PeerDisconnected('Could not read the export connection', 0, $error); }
            if ($piece === false) throw new PeerDisconnected('Could not read the export connection');
            if ($piece !== '') {
                $this->inputBuffer .= $piece;
                $deadline = $this->now() + $this->idleSeconds;
                return true;
            }
            if (feof($this->input)) return false;
            $this->wait(false, $deadline);
        }
    }
    public function readLine(): string|false {
        $deadline = $this->now() + $this->idleSeconds;
        while (true) {
            $newline = strpos($this->inputBuffer, "\n");
            if ($newline !== false) {
                if ($newline >= 1048576) fail('A protocol frame is too long or incomplete');
                $line = substr($this->inputBuffer, 0, $newline + 1);
                $this->inputBuffer = substr($this->inputBuffer, $newline + 1);
                return $line;
            }
            if (strlen($this->inputBuffer) >= 1048576) fail('A protocol frame is too long or incomplete');
            if (!$this->receive($deadline)) {
                if ($this->inputBuffer !== '') throw new PeerDisconnected('The connection ended during a protocol frame');
                return false;
            }
        }
    }
    public function readExact(int $length): string {
        $deadline = $this->now() + $this->idleSeconds;
        $data = '';
        while (strlen($data) < $length) {
            $take = min($length - strlen($data), strlen($this->inputBuffer));
            if ($take > 0) {
                $data .= substr($this->inputBuffer, 0, $take);
                $this->inputBuffer = substr($this->inputBuffer, $take);
            } elseif (!$this->receive($deadline)) {
                throw new PeerDisconnected('The connection ended before the complete chunk arrived');
            }
        }
        return $data;
    }
    public function write(string $data): void {
        $deadline = $this->now() + $this->idleSeconds;
        $position = 0;
        while ($position < strlen($data)) {
            try { $written = fwrite($this->output, substr($data, $position, 65536)); }
            catch (Throwable $error) { throw new PeerDisconnected('Could not write the export connection', 0, $error); }
            if ($written === false) throw new PeerDisconnected('Could not write the export connection');
            if ($written > 0) {
                $position += $written;
                $deadline = $this->now() + $this->idleSeconds;
            } else {
                $this->wait(true, $deadline);
            }
        }
    }
}

function reply(array $value): void {
    global $protocol;
    $line = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_THROW_ON_ERROR) . "\n";
    $protocol->write($line);
}

function fail(string $message): never { throw new RuntimeException($message); }

function statPath(string $path): array|false {
    clearstatcache(true, $path);
    // lstat intentionally checks the link itself, including dangling links.
    if (!file_exists($path) && !is_link($path)) return false;
    $stat = lstat($path);
    if ($stat === false) fail('Could not inspect an export path');
    return $stat;
}

function typeIs(array $stat, int $type): bool { return ($stat['mode'] & 0170000) === $type; }

final class BatchContext {
    private array $directories = [];
    private bool $active = true;
    public function __construct(private SQLite3 $database) {}
    public function requireDatabase(SQLite3 $database): void {
        if (!$this->active || $database !== $this->database) fail('Invalid batch transaction context');
    }
    public function deferDirectory(string $path): void {
        if (!$this->active) fail('The batch transaction has already ended');
        $this->directories[$path] = true;
    }
    public function syncDirectories(): void {
        $paths = array_keys($this->directories);
        usort($paths, static fn(string $left, string $right): int => strlen($right) <=> strlen($left));
        foreach ($paths as $path) {
            $stat = statPath($path);
            if ($stat === false || !typeIs($stat, 0040000)) fail('A batch directory changed before synchronization');
            syncDirectory($path);
        }
        $this->directories = [];
    }
    public function close(): void { $this->active = false; }
}

function batchCapabilities(): array {
    return ['batch' => ['version' => 1, 'maxEntries' => 64, 'maxFileBytes' => 524288, 'maxPayloadBytes' => 524288]];
}

function lifecycleCapabilities(): array {
    return ['lifecycle' => ['version' => 1, 'idleTimeoutSeconds' => PROTOCOL_IDLE_SECONDS, 'heartbeat' => true, 'close' => true]];
}

function syncDirectory(string $path, ?BatchContext $batch = null): void {
    if ($batch !== null) { $batch->deferDirectory($path); return; }
    $handle = fopen($path, 'rb');
    if ($handle === false) fail('Could not open an export directory for synchronization');
    try { if (!fsync($handle)) fail('Could not synchronize an export directory'); }
    finally { fclose($handle); }
}

function ensureDirectory(string $path, ?BatchContext $batch = null): void {
    $stat = statPath($path);
    if ($stat === false) {
        if (!mkdir($path, 0700)) fail('Could not create an export directory');
        syncDirectory(dirname($path), $batch);
        $stat = statPath($path);
    }
    if (!typeIs($stat, 0040000)) fail('Export directories must be actual directories, never symlinks');
    if ($batch !== null) {
        // Also cover directories left behind by an interrupted, unacknowledged
        // batch: their existence alone does not prove a durable parent entry.
        syncDirectory($path, $batch);
        syncDirectory(dirname($path), $batch);
    }
}

function checkRoot(string $root): string {
    if ($root === '' || $root[0] !== '/' || str_contains($root, "\0")) fail('Export root must be an absolute dedicated directory');
    $root = rtrim($root, '/');
    if ($root === '') fail('The filesystem root cannot be an export root');
    $current = '';
    foreach (explode('/', substr($root, 1)) as $component) {
        if ($component === '' || $component === '.' || $component === '..') fail('Invalid export root');
        $current .= '/' . $component;
        $stat = statPath($current);
        if ($stat === false || !typeIs($stat, 0040000)) fail('Export root and its parents must be actual existing directories');
    }
    return $root;
}

function relativePath(mixed $value): string {
    if (!is_string($value) || $value === '' || strlen($value) > 32768 || str_contains($value, "\0") || $value[0] === '/') fail('Invalid relative export path');
    foreach (explode('/', $value) as $index => $component) {
        if ($component === '' || $component === '.' || $component === '..' || ($index === 0 && str_starts_with($component, '.pirouter-'))) fail('Invalid or reserved export path');
    }
    return $value;
}

function childPath(string $base, string $relative, bool $createParents = false, ?BatchContext $batch = null): string {
    $current = $base;
    $parts = explode('/', $relative);
    array_pop($parts);
    foreach ($parts as $component) {
        $current .= '/' . $component;
        if ($createParents) ensureDirectory($current, $batch);
        else {
            $stat = statPath($current);
            if ($stat === false || !typeIs($stat, 0040000)) fail('A path parent is missing or is a symlink');
        }
    }
    return $base . '/' . $relative;
}

function integer(array $frame, string $key, int $minimum = 0): int {
    $value = $frame[$key] ?? null;
    if (!is_int($value) || $value < $minimum) fail('Invalid integer: ' . $key);
    return $value;
}

function digest(mixed $value): string {
    if (!is_string($value) || !preg_match('/^[a-fA-F0-9]{64}$/D', $value)) fail('Invalid SHA-256 identity');
    return strtolower($value);
}

function statement(SQLite3 $db, string $sql, array $values = []): SQLite3Result {
    $query = $db->prepare($sql);
    foreach ($values as $key => $value) {
        $query->bindValue($key + 1, $value, is_int($value) ? SQLITE3_INTEGER : ($value === null ? SQLITE3_NULL : SQLITE3_TEXT));
    }
    return $query->execute();
}

function row(SQLite3 $db, string $sql, array $values = []): array|false {
    $result = statement($db, $sql, $values);
    try { return $result->fetchArray(SQLITE3_ASSOC); }
    finally { $result->finalize(); }
}

function execute(SQLite3 $db, string $sql, array $values = []): void {
    statement($db, $sql, $values)->finalize();
}

function transaction(SQLite3 $db, callable $callback, ?BatchContext $batch = null): void {
    if ($batch !== null) { $batch->requireDatabase($db); $callback(); return; }
    $db->exec('BEGIN IMMEDIATE');
    try { $callback(); $db->exec('COMMIT'); }
    catch (Throwable $error) { $db->exec('ROLLBACK'); throw $error; }
}

function counters(SQLite3 $db): array {
    $meta = row($db, 'SELECT bytes_copied, files_copied FROM meta WHERE id=1');
    return ['bytesCopied' => (int)$meta['bytes_copied'], 'filesCopied' => (int)$meta['files_copied']];
}

function invalidateDirectories(SQLite3 $db, string $path): void {
    $parts = explode('/', $path);
    array_pop($parts);
    $parent = '';
    foreach ($parts as $component) {
        $parent = $parent === '' ? $component : $parent . '/' . $component;
        execute($db, "UPDATE entries SET dirs_applied=0 WHERE path=? AND kind='dir' AND dirs_applied=1", [$parent]);
        $changed = $db->changes();
        if ($changed) execute($db, 'UPDATE meta SET applied_dirs=applied_dirs-? WHERE id=1', [$changed]);
    }
}

function changeEntry(SQLite3 $db, array $old, int $offset, bool $complete, ?BatchContext $batch = null): void {
    transaction($db, static function () use ($db, $old, $offset, $complete): void {
        execute($db, 'UPDATE entries SET offset=?, complete=?, publish_checked=0 WHERE path=?', [$offset, (int)$complete, $old['path']]);
        execute($db, 'UPDATE meta SET bytes_copied=bytes_copied+?, files_copied=files_copied+?, checked_entries=checked_entries-?, finalize_manifest=NULL WHERE id=1', [
            $offset - (int)$old['offset'], (int)$complete - (int)$old['complete'], (int)$old['publish_checked'],
        ]);
        invalidateDirectories($db, $old['path']);
    }, $batch);
}

function syncFile($handle): void {
    if (!fflush($handle) || !fsync($handle)) fail('Could not durably write an export file');
}

function writeAll($handle, string $data): void {
    $position = 0;
    $length = strlen($data);
    while ($position < $length) {
        $written = fwrite($handle, substr($data, $position));
        if ($written === false || $written === 0) fail('Could not write an export file');
        $position += $written;
    }
}

function readBytes($handle, int $length): string {
    $data = '';
    while (strlen($data) < $length) {
        $piece = fread($handle, $length - strlen($data));
        if ($piece === false) fail('Could not read an export file or chunk');
        if ($piece === '') break;
        $data .= $piece;
    }
    return $data;
}

function cleanupOwnedTemporaries(string $base): void {
    foreach (['.pirouter-detach-' => 64, '.pirouter-write-' => 24] as $prefix => $digits) {
        foreach (glob($base . '/' . $prefix . '*') ?: [] as $path) {
            if (!preg_match('/^' . preg_quote($prefix, '/') . '[a-f0-9]{' . $digits . '}$/D', basename($path))) continue;
            $stat = statPath($path);
            if ($stat === false) continue;
            if (!typeIs($stat, 0100000) || $stat['nlink'] > 1) fail('Unsafe receiver temporary metadata');
            if (!unlink($path)) fail('Could not remove an interrupted receiver temporary file');
            syncDirectory($base);
        }
    }
}

function writableFile(string $path, string $partial, ?BatchContext $batch = null) {
    $stat = statPath($path);
    if ($stat !== false && !typeIs($stat, 0100000)) fail('A regular export file cannot replace a directory or symlink');
    // Old rsync exports can have hardlinks into completed snapshots. Replace
    // an unfinished linked inode with an empty private file. Copying an entire
    // legacy multi-gigabyte inode here would require an uninterrupted session.
    if ($stat !== false && $stat['nlink'] > 1) {
        // Receiver temporaries live only in its reserved snapshot-root
        // namespace; user folders may legitimately contain similarly named files.
        $temporary = $partial . '/.pirouter-detach-' . hash('sha256', $path);
        $target = fopen($temporary, 'xb');
        try { if ($batch === null) syncFile($target); }
        finally { fclose($target); }
        if (!rename($temporary, $path)) fail('Could not detach a historical hardlink');
        syncDirectory(dirname($path), $batch);
        syncDirectory($partial, $batch);
        $stat = statPath($path);
    }
    if ($stat !== false && !chmod($path, ($stat['mode'] & 0777) | 0600)) fail('Could not make an unfinished file writable');
    $handle = fopen($path, 'c+b');
    if ($handle === false) fail('Could not open an unfinished export file');
    $actual = fstat($handle);
    if (!typeIs($actual, 0100000) || $actual['nlink'] !== 1) { fclose($handle); fail('Unsafe export file'); }
    if ($stat === false) {
        // Batched files are fsynced once after data and final metadata, before
        // the outer transaction commits. Legacy operations retain their syncs.
        if ($batch === null) syncFile($handle);
        syncDirectory(dirname($path), $batch);
    }
    return $handle;
}

function atomicFile(string $path, string $contents): void {
    $existing = statPath($path);
    if ($existing !== false && (!typeIs($existing, 0100000) || $existing['nlink'] > 1)) fail('Unsafe export metadata path');
    $temporary = dirname($path) . '/.pirouter-write-' . bin2hex(random_bytes(12));
    $handle = fopen($temporary, 'xb');
    try { writeAll($handle, $contents); syncFile($handle); }
    finally { fclose($handle); }
    if (!rename($temporary, $path)) fail('Could not publish export metadata');
    syncDirectory(dirname($path));
}

function beginEntry(SQLite3 $db, string $partial, array $frame, ?BatchContext $batch = null): array {
    $relative = relativePath($frame['path'] ?? null);
    $identity = digest($frame['identity'] ?? null);
    $kind = $frame['kind'] ?? null;
    if (!in_array($kind, ['file', 'dir', 'symlink'], true)) fail('Invalid entry kind');
    $size = integer($frame, 'size');
    $mode = integer($frame, 'mode');
    if ($mode > 07777) fail('Invalid file mode');
    $mtime = integer($frame, 'mtime', PHP_INT_MIN);
    $link = $kind === 'symlink' ? ($frame['link'] ?? null) : null;
    if ($kind === 'symlink' && (!is_string($link) || $link === '' || str_contains($link, "\0"))) fail('Invalid symlink target');
    $path = childPath($partial, $relative, true, $batch);
    $old = row($db, 'SELECT * FROM entries WHERE path=?', [$relative]);
    $same = $old !== false && $old['identity'] === $identity && $old['kind'] === $kind
        && (int)$old['size'] === $size && (int)$old['mode'] === $mode && (int)$old['mtime'] === $mtime && $old['link'] === $link;
    $stat = statPath($path);
    $offset = 0;
    $complete = false;
    if ($kind === 'dir') {
        ensureDirectory($path, $batch);
        // Metadata is deferred until publication; parents must remain writable.
        if (!chmod($path, ($mode & 0777) | 0700)) fail('Could not prepare an export directory');
        $complete = true;
    } elseif ($kind === 'symlink') {
        if ($stat !== false && !typeIs($stat, 0120000)) fail('A symlink cannot replace a different entry kind');
        if ($stat === false || readlink($path) !== $link) {
            if ($stat !== false && !unlink($path)) fail('Could not replace an unfinished symlink');
            if (!symlink($link, $path)) fail('Could not create an export symlink');
            syncDirectory(dirname($path), $batch);
        }
        $complete = true;
    } else {
        if ($stat !== false && !typeIs($stat, 0100000)) fail('A regular export file cannot replace a different entry kind');
        if ($same && $stat !== false && $stat['size'] >= (int)$old['offset']
            && (!(bool)$old['complete'] || $stat['mtime'] === $mtime)) {
            $offset = (int)$old['offset'];
            $complete = (bool)$old['complete'] && $stat['size'] === $size;
        }
        if ($stat !== false && $stat['nlink'] > 1 && !$complete) {
            $handle = writableFile($path, $partial, $batch);
            fclose($handle);
            $offset = 0;
        }
        if ($stat === false) { $handle = writableFile($path, $partial, $batch); fclose($handle); }
    }
    if ($batch !== null) syncDirectory(dirname($path), $batch);
    transaction($db, static function () use ($db, $old, $relative, $kind, $identity, $size, $mode, $mtime, $link, $offset, $complete): void {
        execute($db, 'INSERT OR REPLACE INTO entries(path,kind,identity,size,mode,mtime,link,offset,complete,publish_checked,dirs_applied) VALUES(?,?,?,?,?,?,?,?,?,0,0)',
            [$relative, $kind, $identity, $size, $mode, $mtime, $link, $offset, (int)$complete]);
        execute($db, 'UPDATE meta SET bytes_copied=bytes_copied+?, files_copied=files_copied+?, checked_entries=checked_entries-?, applied_dirs=applied_dirs-?, total_entries=total_entries+?, total_dirs=total_dirs+?, finalize_manifest=NULL WHERE id=1', [
            ($kind === 'file' ? $offset : 0) - ($old !== false && $old['kind'] === 'file' ? (int)$old['offset'] : 0),
            ($kind === 'file' ? (int)$complete : 0) - ($old !== false && $old['kind'] === 'file' ? (int)$old['complete'] : 0),
            $old === false ? 0 : (int)$old['publish_checked'], $old === false ? 0 : (int)$old['dirs_applied'],
            $old === false ? 1 : 0, (int)($kind === 'dir') - (int)($old !== false && $old['kind'] === 'dir'),
        ]);
        invalidateDirectories($db, $relative);
    }, $batch);
    return ['ok' => true, 'complete' => $complete, 'offset' => $offset];
}

function chunkEntry(SQLite3 $db, string $partial, array $frame): void {
    global $protocol;
    $relative = relativePath($frame['path'] ?? null);
    $offset = integer($frame, 'offset');
    $length = integer($frame, 'length', 1);
    if ($length > 8 * 1024 * 1024) fail('A chunk cannot exceed 8 MiB');
    $sha = digest($frame['sha256'] ?? null);
    $entry = row($db, 'SELECT * FROM entries WHERE path=?', [$relative]);
    if ($entry === false || $entry['kind'] !== 'file' || (bool)$entry['complete']) fail('Begin an unfinished regular file before sending chunks');
    if ($offset !== (int)$entry['offset'] || $length > (int)$entry['size'] - $offset) fail('Chunk offset or length is outside the expected file');
    $path = childPath($partial, $relative);
    $handle = writableFile($path, $partial);
    try {
        if (fstat($handle)['size'] < $offset) {
            changeEntry($db, $entry, 0, false);
            fail('The unfinished file was truncated; begin it again');
        }
        if (fseek($handle, $offset) !== 0) fail('Could not seek in an export file');
        $existing = readBytes($handle, $length);
        if (strlen($existing) === $length && hash_equals($sha, hash('sha256', $existing))) {
            syncFile($handle);
            changeEntry($db, $entry, $offset + $length, false);
            reply(['ok' => true, 'needData' => false, 'offset' => $offset + $length]);
            return;
        }
        reply(['ok' => true, 'needData' => true, 'offset' => $offset]);
        $data = $protocol->readExact($length);
        if (!hash_equals($sha, hash('sha256', $data))) fail('Chunk SHA-256 mismatch; no checkpoint was advanced');
        if (fseek($handle, $offset) !== 0) fail('Could not seek in an export file');
        writeAll($handle, $data);
        syncFile($handle);
        if (fseek($handle, $offset) !== 0) fail('Could not verify the written chunk');
        $verified = readBytes($handle, $length);
        if (strlen($verified) !== $length || !hash_equals($sha, hash('sha256', $verified))) fail('Written chunk SHA-256 mismatch');
        changeEntry($db, $entry, $offset + $length, false);
        reply(['ok' => true, 'offset' => $offset + $length]);
    } finally { fclose($handle); }
}

function finishEntry(SQLite3 $db, string $partial, array $frame): array {
    $relative = relativePath($frame['path'] ?? null);
    $entry = row($db, 'SELECT * FROM entries WHERE path=?', [$relative]);
    if ($entry === false || $entry['kind'] !== 'file') fail('Begin a regular file before finishing it');
    if ((int)$entry['offset'] !== (int)$entry['size']) fail('Cannot finish a file before every chunk is verified');
    $path = childPath($partial, $relative);
    $handle = writableFile($path, $partial);
    try {
        if (fstat($handle)['size'] < (int)$entry['size']) {
            changeEntry($db, $entry, 0, false);
            fail('The unfinished file was truncated; begin it again');
        }
        if (!ftruncate($handle, (int)$entry['size'])) fail('Could not remove an unfinished file tail');
        if (!chmod($path, (int)$entry['mode']) || !touch($path, (int)$entry['mtime'])) fail('Could not set export file metadata');
        syncFile($handle);
        changeEntry($db, $entry, (int)$entry['size'], true);
    } finally { fclose($handle); }
    return ['ok' => true, 'complete' => true, 'offset' => (int)$entry['size']];
}

function validateBatch(string $partial, array $frame): array {
    $entries = $frame['entries'] ?? null;
    if (!is_array($entries) || !array_is_list($entries) || count($entries) < 1 || count($entries) > 64) fail('A batch must contain between 1 and 64 entries');
    $normalized = [];
    $planned = [];
    $payloadBytes = 0;
    foreach ($entries as $entry) {
        if (!is_array($entry)) fail('Invalid batch entry');
        $path = relativePath($entry['path'] ?? null);
        if (isset($planned[$path])) fail('A batch cannot contain duplicate paths');
        $kind = $entry['kind'] ?? null;
        if (!in_array($kind, ['file', 'dir', 'symlink'], true)) fail('Invalid batch entry kind');
        $identity = digest($entry['identity'] ?? null);
        $size = integer($entry, 'size');
        $mode = integer($entry, 'mode');
        if ($mode > 07777) fail('Invalid file mode');
        $mtime = integer($entry, 'mtime', PHP_INT_MIN);
        $link = $kind === 'symlink' ? ($entry['link'] ?? null) : null;
        if ($kind === 'symlink' && (!is_string($link) || $link === '' || str_contains($link, "\0"))) fail('Invalid symlink target');
        $value = ['path' => $path, 'kind' => $kind, 'identity' => $identity,
            'size' => $size, 'mode' => $mode, 'mtime' => $mtime, 'link' => $link];
        if ($kind === 'file') {
            if ($size > 524288) fail('A batched regular file cannot exceed 512 KiB');
            $encoded = $entry['data'] ?? null;
            if (!is_string($encoded) || strlen($encoded) !== 4 * intdiv($size + 2, 3)) fail('Invalid batched file data');
            $data = base64_decode($encoded, true);
            if ($data === false || base64_encode($data) !== $encoded || strlen($data) !== $size) fail('Batched file data must be exact canonical base64 of its declared size');
            $sha = digest($entry['sha256'] ?? null);
            if (!hash_equals($sha, hash('sha256', $data))) fail('Batched file SHA-256 mismatch');
            $payloadBytes += $size;
            if ($payloadBytes > 524288) fail('A batch cannot exceed 512 KiB of file payload');
            $value['payload'] = $data;
            $value['sha256'] = $sha;
        } elseif (isset($entry['data']) || isset($entry['sha256'])) fail('Only regular files can carry batch payload');
        $planned[$path] = $kind;
        $normalized[] = $value;
    }
    // Validate the entire frame, including later paths and planned ancestors,
    // before modifying the private filesystem or opening a write transaction.
    foreach ($normalized as $entry) {
        $parts = explode('/', $entry['path']);
        $relative = '';
        foreach ($parts as $index => $part) {
            $relative = $relative === '' ? $part : $relative . '/' . $part;
            $leaf = $index === count($parts) - 1;
            if (!$leaf && isset($planned[$relative]) && $planned[$relative] !== 'dir') fail('A batched file or symlink cannot be a parent directory');
            $stat = statPath($partial . '/' . $relative);
            if ($stat === false) continue;
            $type = $leaf ? ['file' => 0100000, 'dir' => 0040000, 'symlink' => 0120000][$entry['kind']] : 0040000;
            if (!typeIs($stat, $type)) fail('A batch path has an unsafe parent or conflicting entry kind');
        }
    }
    return $normalized;
}

function prepareBatchFile(string $partial, array $entry, BatchContext $batch): array {
    $path = childPath($partial, $entry['path']);
    $stat = statPath($path);
    if ($stat === false || !typeIs($stat, 0100000)) fail('A batched file changed kind');
    $reader = fopen($path, 'rb');
    try {
        $existing = readBytes($reader, $entry['size']);
        $sameBytes = fstat($reader)['size'] === $entry['size']
            && strlen($existing) === $entry['size'] && hash_equals($entry['sha256'], hash('sha256', $existing));
    } finally { fclose($reader); }
    // A completed hardlink with exactly matching data and metadata can be
    // verified read-only. Any required write first detaches the historical inode.
    $readOnlyLink = $stat['nlink'] > 1 && $sameBytes
        && ($stat['mode'] & 07777) === $entry['mode'] && $stat['mtime'] === $entry['mtime'];
    $handle = $readOnlyLink ? fopen($path, 'rb') : writableFile($path, $partial, $batch);
    try {
        if (!$readOnlyLink) {
            if (!$sameBytes || $stat['nlink'] > 1) {
                if (fseek($handle, 0) !== 0) fail('Could not seek in a batched file');
                writeAll($handle, $entry['payload']);
            }
            if (fstat($handle)['size'] !== $entry['size'] && !ftruncate($handle, $entry['size'])) fail('Could not truncate a batched file');
            if (!chmod($path, $entry['mode']) || !touch($path, $entry['mtime'])) fail('Could not set batched file metadata');
        }
        // Submit every file in the bounded batch before its first fsync. This
        // gives the filesystem a chance to combine writeback instead of forcing
        // a write-and-wait cycle for each tiny file. No checkpoint advances yet.
        if (!fflush($handle)) fail('Could not flush a batched file');
        return ['handle' => $handle, 'entry' => $entry];
    } catch (Throwable $error) { fclose($handle); throw $error; }
}

function finishBatchFile(SQLite3 $db, array $prepared, BatchContext $batch): void {
    $handle = $prepared['handle'];
    $entry = $prepared['entry'];
    // Every file still receives its own fsync and read-back verification before
    // the batch transaction commits or any acknowledgement is sent.
    syncFile($handle);
    if (fseek($handle, 0) !== 0) fail('Could not verify a batched file');
    $verified = readBytes($handle, $entry['size']);
    if (fstat($handle)['size'] !== $entry['size'] || strlen($verified) !== $entry['size']
        || !hash_equals($entry['sha256'], hash('sha256', $verified))) fail('Batched file read-back SHA-256 mismatch');
    $old = row($db, 'SELECT * FROM entries WHERE path=?', [$entry['path']]);
    changeEntry($db, $old, $entry['size'], true, $batch);
}

function receiveBatch(SQLite3 $db, string $partial, array $frame): array {
    $entries = validateBatch($partial, $frame);
    $batch = new BatchContext($db);
    $completedPaths = [];
    $preparedFiles = [];
    $db->exec('BEGIN IMMEDIATE');
    try {
        foreach ($entries as $entry) {
            beginEntry($db, $partial, $entry, $batch);
            if ($entry['kind'] === 'file') $preparedFiles[$entry['path']] = prepareBatchFile($partial, $entry, $batch);
        }
        foreach ($entries as $entry) {
            if ($entry['kind'] === 'file') finishBatchFile($db, $preparedFiles[$entry['path']], $batch);
            $completedPaths[] = $entry['path'];
        }
        // No checkpoint is visible until every file and required directory
        // entry is durable. A disconnect can only lose the unacknowledged batch.
        $batch->syncDirectories();
        $db->exec('COMMIT');
    } catch (Throwable $error) {
        $db->exec('ROLLBACK');
        throw $error;
    } finally {
        foreach ($preparedFiles as $prepared) fclose($prepared['handle']);
        $batch->close();
    }
    return ['ok' => true, 'batchComplete' => true, 'paths' => $completedPaths] + counters($db);
}

function publishExport(SQLite3 $db, string $root, string $snapshot, string $source, string $partial, string $final, array $frame): array {
    $expectedFiles = integer($frame, 'expectedFiles');
    $expectedEntries = integer($frame, 'expectedEntries');
    $volumes = $frame['volumes'] ?? null;
    if (!is_array($volumes) || !array_is_list($volumes) || count($volumes) === 0) fail('A verified volume manifest is required');
    $declared = [];
    foreach ($volumes as $volume) {
        if (!is_array($volume)) fail('Invalid volume manifest');
        $name = relativePath($volume['name'] ?? null);
        if (str_contains($name, '/') || isset($declared[$name])) fail('Invalid or duplicate volume name');
        $declared[$name] = integer($volume, 'regularFiles', 1);
    }
    $manifest = hash('sha256', json_encode([$expectedFiles, $expectedEntries, $declared], JSON_THROW_ON_ERROR));
    $meta = row($db, 'SELECT * FROM meta WHERE id=1');
    if ($meta['finalize_manifest'] !== $manifest) {
        // Cache these SQL-only manifest checks. Repeating a million-row GROUP
        // BY for each bounded metadata step would defeat resumable finalizing.
        $summary = row($db, "SELECT COUNT(*) AS entries, COALESCE(SUM(CASE WHEN complete=0 THEN 1 ELSE 0 END),0) AS unfinished, COALESCE(SUM(CASE WHEN kind='file' THEN 1 ELSE 0 END),0) AS files FROM entries");
        if ((int)$summary['entries'] !== $expectedEntries || (int)$summary['files'] !== $expectedFiles || (int)$summary['unfinished'] !== 0) fail('The export does not contain every completed expected entry');
        $result = $db->query("SELECT CASE WHEN instr(path,'/')=0 THEN path ELSE substr(path,1,instr(path,'/')-1) END AS volume, SUM(CASE WHEN kind='file' THEN 1 ELSE 0 END) AS files FROM entries GROUP BY volume");
        $seen = [];
        try {
            while ($volume = $result->fetchArray(SQLITE3_ASSOC)) {
                if (!isset($declared[$volume['volume']]) || (int)$volume['files'] !== $declared[$volume['volume']]) fail('Every source volume must contain its expected regular files');
                $seen[$volume['volume']] = true;
            }
        } finally { $result->finalize(); }
        if (count($seen) !== count($declared) || array_sum($declared) !== $expectedFiles) fail('A source volume is missing from the export');
        execute($db, 'UPDATE meta SET finalize_manifest=? WHERE id=1', [$manifest]);
    }
    if (statPath($final) !== false) fail('A completed snapshot folder already exists and will never be overwritten');
    // Each command checks a bounded batch, then durably records the prefix.
    // Reconnecting continues the audit instead of rescanning every file.
    $entries = $db->query('SELECT path,kind,size,mtime,link FROM entries WHERE publish_checked=0 ORDER BY path LIMIT 256');
    $checked = [];
    try {
        while ($entry = $entries->fetchArray(SQLITE3_ASSOC)) {
            $path = childPath($partial, $entry['path']);
            $stat = statPath($path);
            $type = ['file' => 0100000, 'dir' => 0040000, 'symlink' => 0120000][$entry['kind']];
            if ($stat === false || !typeIs($stat, $type)) fail('A completed export entry is missing or changed kind');
            if ($entry['kind'] === 'file' && ($stat['size'] !== (int)$entry['size'] || $stat['mtime'] !== (int)$entry['mtime'])) fail('A completed export file changed after verification');
            if ($entry['kind'] === 'symlink' && readlink($path) !== $entry['link']) fail('A completed export symlink changed after verification');
            $checked[] = $entry['path'];
        }
    } finally { $entries->finalize(); }
    if ($checked) transaction($db, static function () use ($db, $checked): void {
        foreach ($checked as $path) execute($db, 'UPDATE entries SET publish_checked=1 WHERE path=?', [$path]);
        execute($db, 'UPDATE meta SET checked_entries=checked_entries+? WHERE id=1', [count($checked)]);
    });
    $meta = row($db, 'SELECT * FROM meta WHERE id=1');
    $progress = ['ok' => true, 'published' => false, 'finalizing' => true,
        'checkedEntries' => (int)$meta['checked_entries'], 'totalEntries' => (int)$meta['total_entries']];
    if ((int)$meta['checked_entries'] < (int)$meta['total_entries']) return $progress + counters($db);
    $directories = $db->query("SELECT path,mode,mtime FROM entries WHERE kind='dir' AND dirs_applied=0 ORDER BY length(path) DESC LIMIT 256");
    $applied = [];
    try {
        while ($entry = $directories->fetchArray(SQLITE3_ASSOC)) {
            $path = childPath($partial, $entry['path']);
            $stat = statPath($path);
            if ($stat === false || !typeIs($stat, 0040000)) fail('An export directory is missing or unsafe');
            if (!chmod($path, (int)$entry['mode']) || !touch($path, (int)$entry['mtime'])) fail('Could not finalize directory metadata');
            syncDirectory($path);
            $applied[] = $entry['path'];
        }
    } finally { $directories->finalize(); }
    if ($applied) transaction($db, static function () use ($db, $applied): void {
        foreach ($applied as $path) execute($db, 'UPDATE entries SET dirs_applied=1 WHERE path=?', [$path]);
        execute($db, 'UPDATE meta SET applied_dirs=applied_dirs+? WHERE id=1', [count($applied)]);
    });
    $meta = row($db, 'SELECT * FROM meta WHERE id=1');
    if ((int)$meta['applied_dirs'] < (int)$meta['total_dirs']) return $progress + counters($db);
    $counts = counters($db);
    $receipt = ['version' => 2, 'snapshot' => $snapshot, 'sourceId' => $source, 'verified' => true] + $counts;
    atomicFile($partial . '/.pirouter-volumes.json', json_encode($volumes, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n");
    atomicFile($partial . '/.pirouter-verified-v2', json_encode($receipt, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n");
    atomicFile($partial . '/.pirouter-complete', json_encode($receipt, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n");
    if (!chmod($partial, 0755)) fail('Could not make the published snapshot browsable');
    syncDirectory($partial);
    // Close the database before renaming its directory. DELETE journaling leaves
    // no open WAL or process-specific journal to move into a published export.
    $db->close();
    if (statPath($final) !== false || !rename($partial, $final)) fail('Could not publish the verified snapshot without replacing history');
    syncDirectory(dirname($final));
    atomicFile($root . '/LATEST.txt', $snapshot . "\n");
    return ['ok' => true, 'published' => true] + $counts;
}

try {
    $protocol = new ProtocolIO(STDIN, STDOUT, PROTOCOL_IDLE_SECONDS);
    if ($argc !== 4) fail('Usage: receiver ROOT SNAPSHOT SOURCE_ID');
    $root = checkRoot($argv[1]);
    $snapshot = $argv[2];
    if (!preg_match('/^\d{4}-\d{2}-\d{2}-\d{6}$/D', $snapshot)) fail('Invalid snapshot name');
    $source = digest($argv[3]);
    $lockPath = $root . '/.portable-export.flock';
    $lockStat = statPath($lockPath);
    if ($lockStat !== false && (!typeIs($lockStat, 0100000) || $lockStat['nlink'] > 1)) fail('Unsafe export lock file');
    $lock = fopen($lockPath, 'c+b');
    $openedLock = fstat($lock);
    $currentLock = statPath($lockPath);
    if (!typeIs($openedLock, 0100000) || $openedLock['nlink'] !== 1 || $openedLock['ino'] !== $currentLock['ino'] || $openedLock['dev'] !== $currentLock['dev']) fail('Unsafe export lock file');
    if (!flock($lock, LOCK_EX | LOCK_NB)) fail('Another export currently holds the destination lock');
    cleanupOwnedTemporaries($root);
    ensureDirectory($root . '/snapshots');
    $partial = $root . '/snapshots/.' . $snapshot . '.partial';
    $final = $root . '/snapshots/' . $snapshot;
    $finalStat = statPath($final);
    if ($finalStat !== false) {
        if (!typeIs($finalStat, 0040000)) fail('The completed snapshot path is unsafe');
        foreach (['.pirouter-verified-v2', '.pirouter-complete'] as $marker) {
            $markerStat = statPath($final . '/' . $marker);
            if ($markerStat === false || !typeIs($markerStat, 0100000)) fail('A snapshot folder already exists without verified completion markers');
        }
        $counts = ['bytesCopied' => 0, 'filesCopied' => 0];
        $databaseStat = statPath($final . '/.pirouter-resume.sqlite');
        if ($databaseStat !== false) {
            if (!typeIs($databaseStat, 0100000) || $databaseStat['nlink'] > 1) fail('Unsafe export metadata database');
            $readOnly = new SQLite3($final . '/.pirouter-resume.sqlite', SQLITE3_OPEN_READONLY);
            $readOnly->enableExceptions(true);
            $meta = row($readOnly, 'SELECT source_id FROM meta WHERE id=1');
            if ($meta === false || $meta['source_id'] !== $source) fail('This snapshot belongs to a different source identity');
            $counts = counters($readOnly);
            $readOnly->close();
        }
        // Recover a disconnect/crash after the directory rename but before the
        // LATEST receipt, without changing anything inside the final folder.
        $latestStat = statPath($root . '/LATEST.txt');
        if ($latestStat === false || (typeIs($latestStat, 0100000) && strcmp(trim(file_get_contents($root . '/LATEST.txt')), $snapshot) <= 0)) {
            atomicFile($root . '/LATEST.txt', $snapshot . "\n");
        } elseif (!typeIs($latestStat, 0100000)) fail('Unsafe latest snapshot receipt');
        reply(['ok' => true, 'published' => true] + $counts + batchCapabilities() + lifecycleCapabilities());
        exit(0);
    }
    ensureDirectory($partial);
    cleanupOwnedTemporaries($partial);
    $dbPath = $partial . '/.pirouter-resume.sqlite';
    foreach ([$dbPath, $dbPath . '-journal', $dbPath . '-wal', $dbPath . '-shm'] as $metadataPath) {
        $stat = statPath($metadataPath);
        if ($stat !== false && (!typeIs($stat, 0100000) || $stat['nlink'] > 1)) fail('Unsafe export metadata database');
    }
    $db = new SQLite3($dbPath, SQLITE3_OPEN_READWRITE | SQLITE3_OPEN_CREATE);
    $db->enableExceptions(true);
    $db->busyTimeout(5000);
    // EXTRA includes FULL synchronization and also syncs the containing
    // directory after a DELETE-journal commit unlinks its journal file.
    $db->exec('PRAGMA journal_mode=DELETE; PRAGMA synchronous=EXTRA;');
    $db->exec('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1), source_id TEXT NOT NULL, snapshot TEXT NOT NULL, bytes_copied INTEGER NOT NULL, files_copied INTEGER NOT NULL, finalize_manifest TEXT, checked_entries INTEGER NOT NULL DEFAULT 0, applied_dirs INTEGER NOT NULL DEFAULT 0, total_entries INTEGER NOT NULL DEFAULT 0, total_dirs INTEGER NOT NULL DEFAULT 0)');
    $db->exec('CREATE TABLE IF NOT EXISTS entries(path TEXT PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL, size INTEGER NOT NULL, mode INTEGER NOT NULL, mtime INTEGER NOT NULL, link TEXT, offset INTEGER NOT NULL, complete INTEGER NOT NULL, publish_checked INTEGER NOT NULL DEFAULT 0, dirs_applied INTEGER NOT NULL DEFAULT 0)');
    $meta = row($db, 'SELECT source_id,snapshot FROM meta WHERE id=1');
    if ($meta === false) execute($db, 'INSERT INTO meta(id,source_id,snapshot,bytes_copied,files_copied) VALUES(1,?,?,0,0)', [$source, $snapshot]);
    elseif ($meta['source_id'] !== $source || $meta['snapshot'] !== $snapshot) fail('This snapshot belongs to a different source identity');
    transaction($db, static function () use ($db): void {
        $migrated = false;
        foreach (['meta' => ['finalize_manifest' => 'TEXT', 'checked_entries' => 'INTEGER NOT NULL DEFAULT 0', 'applied_dirs' => 'INTEGER NOT NULL DEFAULT 0', 'total_entries' => 'INTEGER NOT NULL DEFAULT 0', 'total_dirs' => 'INTEGER NOT NULL DEFAULT 0'],
            'entries' => ['publish_checked' => 'INTEGER NOT NULL DEFAULT 0', 'dirs_applied' => 'INTEGER NOT NULL DEFAULT 0']] as $table => $columns) {
            $present = [];
            $info = $db->query('PRAGMA table_info(' . $table . ')');
            while ($column = $info->fetchArray(SQLITE3_ASSOC)) $present[$column['name']] = true;
            $info->finalize();
            foreach ($columns as $name => $definition) if (!isset($present[$name])) {
                $db->exec('ALTER TABLE ' . $table . ' ADD COLUMN ' . $name . ' ' . $definition);
                $migrated = true;
            }
        }
        if ($migrated) $db->exec("UPDATE meta SET checked_entries=(SELECT COALESCE(SUM(publish_checked),0) FROM entries), applied_dirs=(SELECT COALESCE(SUM(dirs_applied),0) FROM entries), total_entries=(SELECT COUNT(*) FROM entries), total_dirs=(SELECT COUNT(*) FROM entries WHERE kind='dir'), finalize_manifest=NULL WHERE id=1");
    });
    $db->exec('CREATE INDEX IF NOT EXISTS entries_unchecked ON entries(publish_checked,path)');
    $db->exec('CREATE INDEX IF NOT EXISTS entries_unapplied_dirs ON entries(kind,dirs_applied,length(path) DESC)');
    syncDirectory($partial);
    reply(['ok' => true, 'published' => false] + counters($db) + batchCapabilities() + lifecycleCapabilities());
    while (($line = $protocol->readLine()) !== false) {
        try {
            if (!str_ends_with($line, "\n")) fail('A protocol frame is too long or incomplete');
            $frame = json_decode($line, true, 64, JSON_THROW_ON_ERROR);
            if (!is_array($frame)) fail('Protocol frames must be JSON objects');
            $op = $frame['op'] ?? null;
            if ($op === 'begin') reply(beginEntry($db, $partial, $frame));
            elseif ($op === 'chunk') chunkEntry($db, $partial, $frame);
            elseif ($op === 'finish') reply(finishEntry($db, $partial, $frame));
            elseif ($op === 'batch') reply(receiveBatch($db, $partial, $frame));
            elseif ($op === 'status') reply(['ok' => true] + counters($db));
            elseif ($op === 'heartbeat') reply(['ok' => true, 'alive' => true]);
            elseif ($op === 'close') {
                $db->close();
                if (!flock($lock, LOCK_UN)) fail('Could not release the export lock');
                fclose($lock);
                // No filesystem work may follow the unlock. Even if this ACK
                // blocks, the next session can now acquire the unchanged lock.
                reply(['ok' => true, 'closed' => true]);
                exit(0);
            }
            elseif ($op === 'publish') {
                $published = publishExport($db, $root, $snapshot, $source, $partial, $final, $frame);
                reply($published);
                if ($published['published']) exit(0);
            } else fail('Unknown export operation');
        } catch (PeerDisconnected $error) { exit(75); }
        catch (Throwable $error) {
            reply(['ok' => false, 'error' => $error->getMessage()]);
            // An invalid frame or failed write must not leave a process holding
            // the lock after its client has stopped interpreting the stream.
            exit(1);
        }
    }
    $db->close();
} catch (PeerDisconnected $error) { exit(75); }
catch (Throwable $error) {
    try { reply(['ok' => false, 'error' => $error->getMessage()]); }
    catch (Throwable $ignored) { /* The client may already have disconnected. */ }
    exit(1);
}
