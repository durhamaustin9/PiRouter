"""Exercise durable export checkpoints using PHP, locally or over test-only SSH.

Without local PHP, set PORTABLE_RECEIVER_SSH to an SSH command ending in its
destination host. Remote fixtures are confined to /tmp/pirouter-receiver-test.*.
The receiver source is supplied to php -r; nothing is installed on the server.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid


RECEIVER = Path(__file__).resolve().parents[1] / "scripts/portable-export-receiver.php"
SNAPSHOT = "2000-01-03-120000"
SOURCE = "a" * 64
PARTIAL = f"snapshots/.{SNAPSHOT}.partial"
FINAL = f"snapshots/{SNAPSHOT}"
SSH = shlex.split(os.environ.get("PORTABLE_RECEIVER_SSH", ""))
PHP = os.environ.get("PORTABLE_RECEIVER_PHP", "/usr/bin/php" if SSH else "php")

FS_CODE = r'''
$args=json_decode(base64_decode($argv[1]),true,512,JSON_THROW_ON_ERROR);
[$action,$root,$relative,$value]=$args;
if (!str_starts_with($root,"/tmp/pirouter-receiver-test.") && !str_contains($root,"pirouter-receiver-test.")) throw new Exception("Unsafe test fixture");
$path=$root.($relative===""?"":"/".$relative);
function removeFixture($path) {
    if (is_link($path) || is_file($path)) { unlink($path); return; }
    if (!is_dir($path)) return;
    chmod($path,0700);
    foreach(scandir($path) as $item) if($item!=="." && $item!=="..") removeFixture($path."/".$item);
    rmdir($path);
}
$result=true;
if($action==="mkdir") { if(!is_dir($path)) mkdir($path,0700,true); }
elseif($action==="write") { if(!is_dir(dirname($path))) mkdir(dirname($path),0700,true); file_put_contents($path,base64_decode($value)); }
elseif($action==="truncate") { $h=fopen($path,"r+b"); ftruncate($h,$value); fclose($h); }
elseif($action==="sparse") { if(!is_dir(dirname($path))) mkdir(dirname($path),0700,true); $h=fopen($path,"w+b"); fwrite($h,"historical sparse header"); ftruncate($h,$value); fclose($h); }
elseif($action==="prefix") { $h=fopen($path,"rb"); $result=base64_encode(fread($h,$value)); fclose($h); }
elseif($action==="symlink") { if(!is_dir(dirname($path))) mkdir(dirname($path),0700,true); symlink($value,$path); }
elseif($action==="hardlink") { if(!is_dir(dirname($path))) mkdir(dirname($path),0700,true); link($root."/".$value,$path); }
elseif($action==="chmod") chmod($path,$value);
elseif($action==="hash") $result=hash_file("sha256",$path);
elseif($action==="read") $result=base64_encode(file_get_contents($path));
elseif($action==="readlink") $result=readlink($path);
elseif($action==="exists") $result=file_exists($path)||is_link($path);
elseif($action==="stat") { $s=lstat($path); $result=["size"=>$s["size"],"mode"=>$s["mode"]&07777,"mtime"=>$s["mtime"],"nlink"=>$s["nlink"],"ino"=>$s["ino"],"dev"=>$s["dev"]]; }
elseif($action==="seedEntries") {
    $db=new SQLite3($path."/.pirouter-resume.sqlite");
    $db->exec("BEGIN");
    $insert=$db->prepare("INSERT INTO entries(path,kind,identity,size,mode,mtime,link,offset,complete,publish_checked,dirs_applied) VALUES(?,?,?,0,?,1777777777,NULL,0,1,0,0)");
    if(!is_dir($path."/Data")) mkdir($path."/Data",0700,true);
    $items=[["Data","dir",0755]];
    for($i=0;$i<$value;$i++) {
        $dir=sprintf("Data/d%04d",$i); mkdir($path."/".$dir,0700);
        file_put_contents($path."/".$dir."/file",""); touch($path."/".$dir."/file",1777777777);
        $items[]=[$dir,"dir",0755]; $items[]=[$dir."/file","file",0644];
    }
    foreach($items as [$name,$kind,$mode]) {
        $insert->bindValue(1,$name,SQLITE3_TEXT); $insert->bindValue(2,$kind,SQLITE3_TEXT);
        $insert->bindValue(3,hash("sha256",$name),SQLITE3_TEXT); $insert->bindValue(4,$mode,SQLITE3_INTEGER);
        $insert->execute()->finalize();
    }
    $db->exec("UPDATE meta SET files_copied=".$value.",total_entries=".count($items).",total_dirs=".($value+1));
    $db->exec("COMMIT"); $db->close();
}
elseif($action==="publicationState") {
    $db=new SQLite3($path."/.pirouter-resume.sqlite",SQLITE3_OPEN_READONLY);
    $result=$db->querySingle("SELECT checked_entries,applied_dirs,total_entries,total_dirs FROM meta",true);
    $db->close();
}
elseif($action==="checkpoint") {
    $db=new SQLite3($path."/.pirouter-resume.sqlite",SQLITE3_OPEN_READONLY);
    $db->busyTimeout(5000);
    $result=$db->querySingle("SELECT bytes_copied,files_copied,total_entries FROM meta",true);
    $db->close();
}
elseif($action==="remove") removeFixture($path);
else throw new Exception("Unknown test operation");
echo json_encode($result,JSON_THROW_ON_ERROR),"\n";
'''


def php_command(code, *args):
    command = [PHP, "-r", code, *map(str, args)]
    return [*SSH, shlex.join(command)] if SSH else command


class Session:
    def __init__(self, fixture, source=SOURCE, code=None):
        self.process = subprocess.Popen(
            php_command(code if code is not None else RECEIVER.read_text(), fixture.root, SNAPSHOT, source),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        fixture.sessions.append(self)
        self.ready = self.read()

    def read(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(15):
                raise AssertionError("Receiver did not respond within 15 seconds")
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read().decode(errors="replace")
            raise AssertionError(f"Receiver closed without a JSON response: {stderr}")
        return json.loads(line)

    def send(self, frame):
        data = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
        self.write(data)

    def write(self, data):
        view = memoryview(data)
        while view:
            size = self.process.stdin.write(view)
            if not size:
                raise AssertionError("Could not send test input")
            view = view[size:]
        self.process.stdin.flush()

    def command(self, frame):
        self.send(frame)
        return self.read()

    def begin(self, data, path="Data/file", identity=None, **overrides):
        frame = {
            "op": "begin", "path": path, "kind": "file",
            "identity": identity or hashlib.sha256(data).hexdigest(),
            "size": len(data), "mode": 0o644, "mtime": 1777777777,
            **overrides,
        }
        return self.command(frame)

    def chunk(self, data, offset=0, path="Data/file", payload=None):
        response = self.command({
            "op": "chunk", "path": path, "offset": offset,
            "length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
        if response.get("ok") and response.get("needData"):
            self.write(data if payload is None else payload)
            return response, self.read()
        return response, response

    def finish(self, path="Data/file"):
        return self.command({"op": "finish", "path": path})

    def publish(self, files=1, entries=1, volumes=None):
        return self.command({
            "op": "publish", "expectedFiles": files, "expectedEntries": entries,
            "volumes": volumes or [{"name": "Data", "regularFiles": files}],
        })

    def close(self, kill=False):
        if self.process.poll() is None:
            if not self.process.stdin.closed:
                self.process.stdin.close()
            if kill:
                self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()


@unittest.skipUnless(SSH or shutil.which(PHP), "PHP CLI is needed (or set PORTABLE_RECEIVER_SSH)")
class PortableReceiver(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.temporary = None
        if SSH:
            self.root = f"/tmp/pirouter-receiver-test.{uuid.uuid4().hex}"
        else:
            self.temporary = tempfile.TemporaryDirectory(prefix="pirouter-receiver-test.")
            self.root = str(Path(self.temporary.name).resolve())
        self.fs("mkdir")

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.fs("remove")
        if self.temporary:
            self.temporary.cleanup()

    def fs(self, action, path="", value=None):
        if isinstance(value, bytes):
            value = base64.b64encode(value).decode()
        encoded = base64.b64encode(json.dumps([action, self.root, path, value]).encode()).decode()
        result = subprocess.run(php_command(FS_CODE, encoded), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return json.loads(result.stdout)

    def session(self, source=SOURCE, code=None):
        return Session(self, source, code=code)

    @staticmethod
    def batch_entry(path, data=b'', kind='file', **overrides):
        entry = dict(path=path, kind=kind, identity=hashlib.sha256(path.encode() + data).hexdigest(),
                     size=len(data), mode=0o755 if kind == 'dir' else 0o644, mtime=1777777777)
        if kind == 'file':
            entry.update(data=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())
        entry.update(overrides)
        return entry

    def assert_ok(self, response):
        self.assertTrue(response.get("ok"), response)
        return response

    def test_disconnect_mid_chunk_keeps_only_acknowledged_offset_and_resumes(self):
        chunks = [os.urandom(64 * 1024) for _ in range(3)]
        data = b"".join(chunks)
        first = self.session()
        self.assert_ok(first.ready)
        self.assert_ok(first.begin(data))
        self.assert_ok(first.chunk(chunks[0])[1])
        self.assert_ok(first.command({
            "op": "chunk", "path": "Data/file", "offset": len(chunks[0]),
            "length": len(chunks[1]), "sha256": hashlib.sha256(chunks[1]).hexdigest(),
        }))
        first.write(chunks[1][:1234])
        first.close()
        second = self.session()
        self.assertEqual(second.ready["bytesCopied"], len(chunks[0]))
        self.assertEqual(second.begin(data)["offset"], len(chunks[0]))
        self.assert_ok(second.chunk(chunks[1], len(chunks[0]))[1])
        second.close()
        third = self.session()
        self.assertEqual(third.begin(data)["offset"], len(chunks[0]) * 2)
        self.assert_ok(third.chunk(chunks[2], len(chunks[0]) * 2)[1])
        self.assert_ok(third.finish())
        published = self.assert_ok(third.publish())
        self.assertEqual(published["bytesCopied"], len(data))
        self.assertEqual(published["filesCopied"], 1)
        self.assertEqual(self.fs("stat", FINAL)["mode"], 0o755)
        self.assertEqual(self.fs("hash", FINAL + "/Data/file"), hashlib.sha256(data).hexdigest())
        third.close()
        done = self.session()
        self.assertTrue(self.assert_ok(done.ready)["published"])
        self.assertEqual(done.ready["bytesCopied"], len(data))

    def test_same_size_corrupt_legacy_file_requires_retransmission(self):
        data = os.urandom(65536)
        self.fs("write", PARTIAL + "/Data/file", b"x" * len(data))
        receiver = self.session()
        self.assertEqual(receiver.begin(data)["offset"], 0)
        initial, complete = receiver.chunk(data)
        self.assertTrue(initial["needData"])
        self.assert_ok(complete)
        self.assert_ok(receiver.finish())
        self.assertEqual(self.fs("hash", PARTIAL + "/Data/file"), hashlib.sha256(data).hexdigest())

    def test_matching_legacy_chunks_are_verified_without_upload_and_old_tail_removed(self):
        data = b"known legacy bytes" * 100
        self.fs("write", PARTIAL + "/Data/file", data + b"obsolete tail")
        receiver = self.session()
        self.assertEqual(receiver.begin(data)["offset"], 0)
        initial, _ = receiver.chunk(data)
        self.assertFalse(self.assert_ok(initial)["needData"])
        self.assertEqual(initial["offset"], len(data))
        self.assertEqual(receiver.command({"op": "status"})["bytesCopied"], len(data))
        self.assert_ok(receiver.finish())
        self.assertEqual(self.fs("stat", PARTIAL + "/Data/file")["size"], len(data))

    def test_truncated_checkpoint_resets_to_zero_safely(self):
        data = b"a" * 16384 + b"b" * 16384
        first = self.session()
        self.assert_ok(first.begin(data))
        self.assert_ok(first.chunk(data[:16384])[1])
        first.close()
        self.fs("truncate", PARTIAL + "/Data/file", 4000)
        second = self.session()
        self.assertEqual(second.begin(data)["offset"], 0)
        self.assertEqual(second.command({"op": "status"})["bytesCopied"], 0)
        self.assert_ok(second.chunk(data)[1])
        self.assert_ok(second.finish())
        self.assertEqual(self.fs("hash", PARTIAL + "/Data/file"), hashlib.sha256(data).hexdigest())

    def test_publish_early_is_blocked(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"unfinished"))
        self.assertFalse(receiver.publish()["ok"])
        self.assertFalse(self.fs("exists", FINAL))
        self.assertFalse(self.fs("exists", PARTIAL + "/.pirouter-complete"))

    def test_oversized_and_out_of_order_chunks_are_rejected(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"", size=8 * 1024 * 1024 + 1))
        rejected = receiver.command({
            "op": "chunk", "path": "Data/file", "offset": 0,
            "length": 8 * 1024 * 1024 + 1, "sha256": "a" * 64,
        })
        self.assertFalse(rejected["ok"])
        receiver.close()
        resumed = self.session()
        self.assert_ok(resumed.begin(b"bytes"))
        self.assertFalse(resumed.chunk(b"bytes", offset=1)[0]["ok"])

    def test_publish_refuses_a_missing_or_empty_declared_volume(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"data"))
        self.assert_ok(receiver.chunk(b"data")[1])
        self.assert_ok(receiver.finish())
        result = receiver.publish(volumes=[
            {"name": "Data", "regularFiles": 1},
            {"name": "Missing main drive", "regularFiles": 1},
        ])
        self.assertFalse(result["ok"])
        self.assertFalse(self.fs("exists", FINAL))

    def test_source_identity_mismatch_is_refused(self):
        first = self.session()
        self.assert_ok(first.ready)
        self.assert_ok(first.begin(b"data"))
        first.close()
        mismatch = self.session("b" * 64)
        self.assertFalse(mismatch.ready["ok"])
        self.assertIn("different source", mismatch.ready["error"])

    def test_traversal_and_reserved_root_metadata_are_rejected(self):
        for path in ("../escape", "/absolute", "Data/../escape", "Data//file", "Data/\x00file", ".pirouter-resume.sqlite"):
            with self.subTest(path=path):
                receiver = self.session()
                self.assert_ok(receiver.ready)
                self.assertFalse(receiver.begin(b"x", path=path)["ok"])
                receiver.close()
        nested = self.session()
        self.assert_ok(nested.begin(b"x", path="Data/.pirouter-user-file"))

    def test_symlink_parent_and_symlink_root_are_rejected(self):
        self.fs("mkdir", "outside")
        self.fs("mkdir", PARTIAL)
        self.fs("symlink", PARTIAL + "/escape", self.root + "/outside")
        receiver = self.session()
        self.assertFalse(receiver.begin(b"x", path="escape/file")["ok"])
        self.assertFalse(self.fs("exists", "outside/file"))
        receiver.close()
        self.fs("symlink", "root-alias", self.root)
        original = self.root
        try:
            self.root = original + "/root-alias"
            self.assertFalse(self.session().ready["ok"])
        finally:
            self.root = original

    def test_live_lock_cannot_be_stolen_and_crashed_owner_releases_it(self):
        first = self.session()
        self.assert_ok(first.ready)
        blocked = self.session()
        self.assertFalse(blocked.ready["ok"])
        self.assertIn("lock", blocked.ready["error"])
        blocked.close()
        first.close(kill=True)
        deadline = time.monotonic() + 5
        while True:
            resumed = self.session()
            if resumed.ready.get("ok"):
                break
            resumed.close()
            self.assertLess(time.monotonic(), deadline, resumed.ready)
            time.sleep(.1)
        self.assert_ok(resumed.ready)

    def test_historical_hardlink_is_detached_before_writes_and_metadata_changes(self):
        old = b"historical" * 1000
        new = b"replacement" * 1000
        historical = "snapshots/2000-01-02-010101/Data/file"
        self.fs("write", historical, old)
        self.fs("chmod", historical, 0o444)
        self.fs("hardlink", PARTIAL + "/Data/file", historical)
        receiver = self.session()
        self.assert_ok(receiver.begin(new))
        self.assert_ok(receiver.chunk(new)[1])
        self.assert_ok(receiver.finish())
        self.assertEqual(self.fs("hash", historical), hashlib.sha256(old).hexdigest())
        self.assertEqual(self.fs("stat", historical)["mode"], 0o444)
        self.assertEqual(self.fs("stat", PARTIAL + "/Data/file")["nlink"], 1)

    def test_large_legacy_hardlink_is_replaced_without_copying_its_contents(self):
        historical = "snapshots/2000-01-02-010101/Data/huge-file"
        self.fs("sparse", historical, 8 * 1024 * 1024 * 1024)
        self.fs("hardlink", PARTIAL + "/Data/file", historical)
        receiver = self.session()
        data = b"new independently verified bytes"
        response = self.assert_ok(receiver.begin(data))
        self.assertEqual(response["offset"], 0)
        self.assertEqual(self.fs("stat", PARTIAL + "/Data/file")["size"], 0)
        self.assertEqual(self.fs("stat", historical)["size"], 8 * 1024 * 1024 * 1024)
        self.assertEqual(base64.b64decode(self.fs("prefix", historical, 24)), b"historical sparse header")
        self.assert_ok(receiver.chunk(data)[1])
        self.assert_ok(receiver.finish())
        self.assertEqual(self.fs("hash", PARTIAL + "/Data/file"), hashlib.sha256(data).hexdigest())

    def test_corrupt_chunk_payload_never_advances_checkpoint(self):
        data = b"verified bytes" * 100
        first = self.session()
        self.assert_ok(first.begin(data))
        _, result = first.chunk(data, payload=b"x" * len(data))
        self.assertFalse(result["ok"])
        first.close()
        resumed = self.session()
        self.assertEqual(resumed.ready["bytesCopied"], 0)
        self.assertEqual(resumed.begin(data)["offset"], 0)
        self.assert_ok(resumed.chunk(data)[1])

    def test_published_folder_is_never_overwritten(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"original"))
        self.assert_ok(receiver.chunk(b"original")[1])
        self.assert_ok(receiver.finish())
        self.assert_ok(receiver.publish())
        receiver.close()
        original_hash = self.fs("hash", FINAL + "/Data/file")
        self.assertTrue(self.session().ready["published"])
        self.assertFalse(self.session("c" * 64).ready["ok"])
        self.assertEqual(self.fs("hash", FINAL + "/Data/file"), original_hash)
        self.assertFalse(self.fs("exists", PARTIAL))
        self.assertEqual(base64.b64decode(self.fs("read", "LATEST.txt")), (SNAPSHOT + "\n").encode())

    def test_unverified_existing_final_folder_is_never_overwritten(self):
        self.fs("write", FINAL + "/Data/file", b"keep existing")
        self.assertFalse(self.session().ready["ok"])
        self.assertEqual(self.fs("hash", FINAL + "/Data/file"), hashlib.sha256(b"keep existing").hexdigest())

    def test_publish_rechecks_completed_file_metadata_after_reconnect(self):
        first = self.session()
        self.assert_ok(first.begin(b"completed bytes"))
        self.assert_ok(first.chunk(b"completed bytes")[1])
        self.assert_ok(first.finish())
        first.close()
        self.fs("truncate", PARTIAL + "/Data/file", 3)
        resumed = self.session()
        self.assertFalse(resumed.publish()["ok"])
        self.assertFalse(self.fs("exists", FINAL))

    def test_empty_files_directories_and_absolute_symlinks_publish_as_browsable_entries(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"", kind="dir", path="Data", mode=0o755))
        self.assert_ok(receiver.begin(b"", path="Data/empty"))
        self.assert_ok(receiver.finish("Data/empty"))
        self.assert_ok(receiver.begin(b"", path="Data/link", kind="symlink", link="/unfollowed/absolute/path"))
        result = self.assert_ok(receiver.publish(entries=3))
        self.assertEqual(result["filesCopied"], 1)
        self.assertEqual(result["bytesCopied"], 0)
        self.assertEqual(self.fs("readlink", FINAL + "/Data/link"), "/unfollowed/absolute/path")
        self.assertEqual(self.fs("stat", FINAL + "/Data")["mtime"], 1777777777)

    def test_changed_entry_identity_resets_checkpoint_and_uses_new_chunk_hash(self):
        first = self.session()
        self.assert_ok(first.begin(b"old data"))
        self.assert_ok(first.chunk(b"old data")[1])
        self.assert_ok(first.finish())
        first.close()
        resumed = self.session()
        self.assertEqual(resumed.begin(b"new data")["offset"], 0)
        self.assertEqual(resumed.command({"op": "status"})["filesCopied"], 0)
        initial, finished = resumed.chunk(b"new data")
        self.assertTrue(initial["needData"])
        self.assert_ok(finished)

    def test_publication_audit_and_directory_metadata_resume_in_bounded_batches(self):
        first = self.session()
        self.assert_ok(first.ready)
        first.close()
        self.fs("seedEntries", PARTIAL, 260)
        first = self.session()
        response = self.assert_ok(first.publish(files=260, entries=521))
        self.assertFalse(response["published"])
        self.assertEqual(response["checkedEntries"], 256)
        first.close()
        self.assertEqual(self.fs("publicationState", PARTIAL)["checked_entries"], 256)
        second = self.session()
        response = self.assert_ok(second.publish(files=260, entries=521))
        self.assertEqual(response["checkedEntries"], 512)
        second.close()
        third = self.session()
        response = self.assert_ok(third.publish(files=260, entries=521))
        self.assertFalse(response["published"])
        self.assertEqual(response["checkedEntries"], 521)
        third.close()
        state = self.fs("publicationState", PARTIAL)
        self.assertEqual(state["applied_dirs"], 256)
        fourth = self.session()
        self.assertTrue(self.assert_ok(fourth.publish(files=260, entries=521))["published"])
        self.assertEqual(self.fs("stat", FINAL + "/Data")["mtime"], 1777777777)
        self.assertEqual(self.fs("stat", FINAL + "/Data/d0259")["mtime"], 1777777777)

    def test_published_startup_repairs_latest_receipt_without_changing_snapshot(self):
        receiver = self.session()
        self.assert_ok(receiver.begin(b"data"))
        self.assert_ok(receiver.chunk(b"data")[1])
        self.assert_ok(receiver.finish())
        self.assert_ok(receiver.publish())
        receiver.close()
        self.fs("write", "LATEST.txt", b"2000-01-02-120000\n")
        before = self.fs("stat", FINAL + "/Data/file")
        self.assertTrue(self.session().ready["published"])
        self.assertEqual(base64.b64decode(self.fs("read", "LATEST.txt")), (SNAPSHOT + "\n").encode())
        self.assertEqual(self.fs("stat", FINAL + "/Data/file"), before)

    def test_only_receiver_root_orphan_temporaries_are_cleaned_after_disconnect(self):
        orphan = ".pirouter-detach-" + "a" * 64
        write_orphan = ".pirouter-write-" + "a" * 24
        self.fs("write", PARTIAL + "/" + orphan, b"interrupted detach")
        self.fs("write", PARTIAL + "/" + write_orphan, b"interrupted receipt")
        self.fs("write", PARTIAL + "/Data/" + orphan, b"user-owned nested name")
        receiver = self.session()
        self.assert_ok(receiver.ready)
        self.assertFalse(self.fs("exists", PARTIAL + "/" + orphan))
        self.assertFalse(self.fs("exists", PARTIAL + "/" + write_orphan))
        self.assertTrue(self.fs("exists", PARTIAL + "/Data/" + orphan))

    def test_batch_advertises_bounds_and_publishes_mixed_browsable_entries(self):
        receiver = self.session()
        self.assertEqual(receiver.ready['batch'], dict(version=1, maxEntries=64,
                         maxFileBytes=524288, maxPayloadBytes=524288))
        entries = [self.batch_entry('Data', kind='dir'),
                   self.batch_entry('Data/nested/a', b'\x00first\xff'),
                   self.batch_entry('Data/empty'),
                   self.batch_entry('Data/link', kind='symlink', link='/unfollowed/absolute')]
        result = self.assert_ok(receiver.command(dict(op='batch', entries=entries)))
        self.assertTrue(result['batchComplete'])
        self.assertEqual(result['paths'], [entry['path'] for entry in entries])
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (7, 2))
        self.assert_ok(receiver.publish(files=2, entries=4))
        receiver.close()
        self.assertEqual(self.fs('hash', FINAL + '/Data/nested/a'), hashlib.sha256(b'\x00first\xff').hexdigest())
        self.assertEqual(self.fs('readlink', FINAL + '/Data/link'), '/unfollowed/absolute')
        self.assertTrue(self.session().ready['published'])

    def test_batch_lost_ack_retry_reuses_verified_bytes_without_double_counting(self):
        first = self.session()
        entries = [self.batch_entry('Data/a', b'alpha'), self.batch_entry('Data/b', b'beta')]
        first.send(dict(op='batch', entries=entries))
        # Observe the durable remote commit without consuming its reply. The
        # client then disconnects as though that acknowledgement was lost.
        deadline = time.monotonic() + 5
        while True:
            state = self.fs('checkpoint', PARTIAL)
            if state['files_copied'] == 2:
                break
            self.assertLess(time.monotonic(), deadline, state)
            time.sleep(.02)
        self.assertEqual((state['bytes_copied'], state['files_copied']), (9, 2))
        first.close()
        code = RECEIVER.read_text().replace('function writeAll($handle, string $data): void {',
            "function writeAll($handle, string $data): void { fail('A retry rewrote verified bytes');")
        retried = self.session(code=code)
        self.assertEqual((retried.ready['bytesCopied'], retried.ready['filesCopied']), (9, 2))
        result = self.assert_ok(retried.command(dict(op='batch', entries=entries)))
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (9, 2))
        self.assertEqual(self.fs('hash', PARTIAL + '/Data/a'), hashlib.sha256(b'alpha').hexdigest())

    def test_batch_crash_after_first_private_file_rolls_back_and_resumes_safely(self):
        marker = "$completedPaths[] = $entry['path'];"
        code = RECEIVER.read_text()
        self.assertEqual(code.count(marker), 1)
        code = code.replace(marker, marker + " if (count($completedPaths) === 1) { posix_kill(getmypid(), 9); }")
        crashed = self.session(code=code)
        entries = [self.batch_entry('Data/a', b'first durable file'), self.batch_entry('Data/b', b'second')]
        crashed.send(dict(op='batch', entries=entries))
        with self.assertRaises(AssertionError):
            crashed.read()
        crashed.close()
        resumed = self.session()
        self.assertEqual((resumed.ready['bytesCopied'], resumed.ready['filesCopied']), (0, 0))
        self.assertEqual(self.fs('hash', PARTIAL + '/Data/a'), hashlib.sha256(b'first durable file').hexdigest())
        result = self.assert_ok(resumed.command(dict(op='batch', entries=entries)))
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (24, 2))

    def test_partial_batch_frame_never_advances_a_checkpoint(self):
        first = self.session()
        entries = [self.batch_entry('Data/a', b'a' * 65536)]
        frame = json.dumps(dict(op='batch', entries=entries)).encode() + b'\n'
        first.write(frame[:len(frame) // 2])
        first.close()
        resumed = self.session()
        self.assertEqual((resumed.ready['bytesCopied'], resumed.ready['filesCopied']), (0, 0))
        self.assertFalse(self.fs('exists', PARTIAL + '/Data/a'))
        self.assert_ok(resumed.command(dict(op='batch', entries=entries)))

    def test_invalid_later_batch_entry_is_rejected_before_creating_earlier_files(self):
        entries = [self.batch_entry('Data/a', b'first'), self.batch_entry('../escape', b'bad')]
        receiver = self.session()
        self.assertFalse(receiver.command(dict(op='batch', entries=entries))['ok'])
        receiver.close()
        self.assertFalse(self.fs('exists', PARTIAL + '/Data/a'))
        self.assertEqual(self.session().ready['bytesCopied'], 0)

    def test_batch_rejects_hash_base64_duplicates_and_all_payload_limits(self):
        bad_hash = self.batch_entry('Data/a', b'abc', sha256='a' * 64)
        bad_base64 = dict(self.batch_entry('Data/a', b'abc'), data='YWJj\n')
        missing_padding = dict(self.batch_entry('Data/a', b'ab'), data='YWI')
        cases = [[], [bad_hash], [bad_base64], [missing_padding],
                 [self.batch_entry('Data/a'), self.batch_entry('Data/a')],
                 [self.batch_entry(f'Data/{index}') for index in range(65)],
                 [self.batch_entry('Data/a', b'x' * 524289)],
                 [self.batch_entry(f'Data/{index}', b'x' * 196608) for index in range(3)]]
        for entries in cases:
            with self.subTest(entries=len(entries)):
                receiver = self.session()
                self.assertFalse(receiver.command(dict(op='batch', entries=entries))['ok'])
                receiver.close()
                self.assertEqual(self.fs('checkpoint', PARTIAL)['bytes_copied'], 0)

    def test_batch_rejects_existing_or_planned_symlink_parents(self):
        self.fs('mkdir', 'outside')
        self.fs('mkdir', PARTIAL)
        self.fs('symlink', PARTIAL + '/escape', self.root + '/outside')
        receiver = self.session()
        self.assertFalse(receiver.command(dict(op='batch', entries=[self.batch_entry('escape/a', b'x')]))['ok'])
        receiver.close()
        receiver = self.session()
        entries = [self.batch_entry('Data/link', kind='symlink', link='/outside'),
                   self.batch_entry('Data/link/a', b'x')]
        self.assertFalse(receiver.command(dict(op='batch', entries=entries))['ok'])
        self.assertFalse(self.fs('exists', PARTIAL + '/Data/link'))
        self.assertFalse(self.fs('exists', 'outside/a'))

    def test_batch_hardlink_writes_never_change_historical_data_or_permissions(self):
        historical = 'snapshots/2000-01-02-010101/Data/a'
        self.fs('write', historical, b'historical bytes')
        self.fs('chmod', historical, 0o444)
        self.fs('hardlink', PARTIAL + '/Data/a', historical)
        receiver = self.session()
        result = self.assert_ok(receiver.command(dict(op='batch', entries=[self.batch_entry('Data/a', b'new bytes')])))
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (9, 1))
        self.assertEqual(self.fs('hash', historical), hashlib.sha256(b'historical bytes').hexdigest())
        self.assertEqual(self.fs('stat', historical)['mode'], 0o444)
        self.assertEqual(self.fs('stat', PARTIAL + '/Data/a')['nlink'], 1)

    def test_batch_ack_is_blocked_by_file_or_parent_sync_failure(self):
        original = RECEIVER.read_text()
        failures = [original.replace('function syncFile($handle): void {',
                    "function syncFile($handle): void { fail('Simulated file fsync failure');"),
                    original.replace('function syncDirectory(string $path, ?BatchContext $batch = null): void {',
                    "function syncDirectory(string $path, ?BatchContext $batch = null): void { if ($batch === null && str_ends_with($path, '/Data')) fail('Simulated parent fsync failure');")]
        for code in failures:
            with self.subTest(file_sync='Simulated file' in code):
                self.assertNotEqual(code, original)
                receiver = self.session(code=code)
                result = receiver.command(dict(op='batch', entries=[self.batch_entry('Data/a', b'not acknowledged')]))
                self.assertFalse(result['ok'])
                receiver.close()
                state = self.fs('checkpoint', PARTIAL)
                self.assertEqual((state['bytes_copied'], state['files_copied'], state['total_entries']), (0, 0, 0))
        resumed = self.session()
        self.assert_ok(resumed.command(dict(op='batch', entries=[self.batch_entry('Data/a', b'not acknowledged')])))

    def test_batch_coalesces_directory_syncs_and_uses_one_data_sync_per_file(self):
        original = RECEIVER.read_text()
        code = original.replace('function syncDirectory(string $path, ?BatchContext $batch = null): void {',
            "function syncDirectory(string $path, ?BatchContext $batch = null): void { if ($batch === null) $GLOBALS['testDirs'][$path] = ($GLOBALS['testDirs'][$path] ?? 0) + 1;")
        code = code.replace('function syncFile($handle): void {',
            "function syncFile($handle): void { $GLOBALS['testFileSyncs'] = ($GLOBALS['testFileSyncs'] ?? 0) + 1;")
        code = code.replace('function reply(array $value): void {',
            "function reply(array $value): void { if ($value['batchComplete'] ?? false) $value['testSyncs'] = ['dirs' => $GLOBALS['testDirs'] ?? [], 'files' => $GLOBALS['testFileSyncs'] ?? 0];")
        receiver = self.session(code=code)
        entries = [self.batch_entry(f'Data/file{index}', bytes([index]) * 50) for index in range(8)]
        result = self.assert_ok(receiver.command(dict(op='batch', entries=entries)))
        self.assertEqual(result['testSyncs']['files'], 8)
        self.assertEqual(result['testSyncs']['dirs'][self.root + '/' + PARTIAL + '/Data'], 1)

    def test_batch_submits_all_payloads_before_waiting_for_the_first_file_sync(self):
        code = RECEIVER.read_text().replace(
            'function receiveBatch(SQLite3 $db, string $partial, array $frame): array {',
            '''function receiveBatch(SQLite3 $db, string $partial, array $frame): array {
                $GLOBALS['testStagedPayloads'] = [];
                foreach ($frame['entries'] as $testEntry) {
                    if ($testEntry['kind'] === 'file')
                        $GLOBALS['testStagedPayloads'][$partial . '/' . $testEntry['path']] = base64_decode($testEntry['data']);
                }''')
        code = code.replace('function syncFile($handle): void {',
            '''function syncFile($handle): void {
                foreach ($GLOBALS['testStagedPayloads'] ?? [] as $testPath => $testData) {
                    if (!file_exists($testPath) || file_get_contents($testPath) !== $testData)
                        fail('A file sync began before the whole batch was submitted');
                }''')
        receiver = self.session(code=code)
        entries = [self.batch_entry('Data/a', b'first'),
                   self.batch_entry('Data/nested/b', b'second' * 10000),
                   self.batch_entry('Data/empty')]
        result = self.assert_ok(receiver.command(dict(op='batch', entries=entries)))
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (60005, 3))

    def test_batch_crash_after_staging_keeps_checkpoint_uncommitted_and_resumes(self):
        code = RECEIVER.read_text().replace(
            'function finishBatchFile(SQLite3 $db, array $prepared, BatchContext $batch): void {',
            '''function finishBatchFile(SQLite3 $db, array $prepared, BatchContext $batch): void {
                posix_kill(getmypid(), 9);''')
        receiver = self.session(code=code)
        entries = [self.batch_entry('Data/a', b'first'), self.batch_entry('Data/b', b'second')]
        receiver.send(dict(op='batch', entries=entries))
        with self.assertRaises(AssertionError):
            receiver.read()
        receiver.close()
        resumed = self.session()
        self.assertEqual((resumed.ready['bytesCopied'], resumed.ready['filesCopied']), (0, 0))
        result = self.assert_ok(resumed.command(dict(op='batch', entries=entries)))
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (11, 2))

    def test_late_batch_file_sync_failure_rolls_back_every_counter(self):
        code = RECEIVER.read_text().replace('function syncFile($handle): void {',
            '''function syncFile($handle): void {
                if (str_ends_with(stream_get_meta_data($handle)['uri'], '/Data/b'))
                    fail('Simulated later file fsync failure');''')
        receiver = self.session(code=code)
        entries = [self.batch_entry('Data/a', b'first'), self.batch_entry('Data/b', b'second')]
        self.assertFalse(receiver.command(dict(op='batch', entries=entries))['ok'])
        receiver.close()
        state = self.fs('checkpoint', PARTIAL)
        self.assertEqual((state['bytes_copied'], state['files_copied'], state['total_entries']), (0, 0, 0))
        resumed = self.session()
        self.assert_ok(resumed.command(dict(op='batch', entries=entries)))

    def test_late_batch_readback_mismatch_prevents_acknowledgement(self):
        code = RECEIVER.read_text().replace(
            'function finishBatchFile(SQLite3 $db, array $prepared, BatchContext $batch): void {',
            '''function finishBatchFile(SQLite3 $db, array $prepared, BatchContext $batch): void {
                if ($prepared['entry']['path'] === 'Data/b') {
                    fseek($prepared['handle'], 0);
                    fwrite($prepared['handle'], 'x');
                }''')
        receiver = self.session(code=code)
        entries = [self.batch_entry('Data/a', b'first'), self.batch_entry('Data/b', b'second')]
        result = receiver.command(dict(op='batch', entries=entries))
        self.assertFalse(result['ok'])
        self.assertIn('read-back SHA-256 mismatch', result['error'])
        receiver.close()
        state = self.fs('checkpoint', PARTIAL)
        self.assertEqual((state['bytes_copied'], state['files_copied'], state['total_entries']), (0, 0, 0))
        resumed = self.session()
        self.assert_ok(resumed.command(dict(op='batch', entries=entries)))

    @staticmethod
    def lifecycle_code(seconds=0.6):
        original = RECEIVER.read_text()
        code = original.replace('const PROTOCOL_IDLE_SECONDS = 180.0;',
                                f'const PROTOCOL_IDLE_SECONDS = {seconds};')
        if code == original:
            raise AssertionError('The protocol timeout injection point changed')
        return code

    def test_lifecycle_idle_pipe_expires_and_releases_unchanged_lock(self):
        receiver = self.session(code=self.lifecycle_code())
        lifecycle = receiver.ready['lifecycle']
        self.assertEqual(lifecycle, dict(version=1, idleTimeoutSeconds=0.6, heartbeat=True, close=True))
        lock = self.fs('stat', '.portable-export.flock')
        self.assertEqual(receiver.process.wait(timeout=5), 75)
        self.assertEqual(receiver.process.stdout.read(), b'')
        resumed = self.session()
        self.assert_ok(resumed.ready)
        current = self.fs('stat', '.portable-export.flock')
        self.assertEqual((current['dev'], current['ino']), (lock['dev'], lock['ino']))

    def test_lifecycle_partial_frame_expires_without_creating_entries(self):
        receiver = self.session(code=self.lifecycle_code())
        receiver.write(b'{"op":"begin","path":"Data/uncommitted')
        self.assertEqual(receiver.process.wait(timeout=5), 75)
        self.assertEqual(receiver.process.stdout.read(), b'')
        self.assertFalse(self.fs('exists', PARTIAL + '/Data/uncommitted'))
        resumed = self.session()
        self.assertEqual((resumed.ready['bytesCopied'], resumed.ready['filesCopied']), (0, 0))

    def test_lifecycle_progressing_frame_outlives_idle_limit(self):
        receiver = self.session(code=self.lifecycle_code())
        frame = b'{"op":"heartbeat"}\n'
        start = time.monotonic()
        for offset in range(0, len(frame), 3):
            time.sleep(0.25)
            receiver.write(frame[offset:offset + 3])
        self.assertGreater(time.monotonic() - start, 0.6)
        self.assertTrue(self.assert_ok(receiver.read())['alive'])

    def test_lifecycle_stalled_raw_chunk_keeps_prior_durable_offset(self):
        receiver = self.session(code=self.lifecycle_code())
        prefix, suffix = b'acknowledged' * 100, b'unacknowledged' * 100
        data = prefix + suffix
        self.assert_ok(receiver.begin(data))
        self.assert_ok(receiver.chunk(prefix)[1])
        self.assertTrue(receiver.command(dict(op='chunk', path='Data/file', offset=len(prefix),
            length=len(suffix), sha256=hashlib.sha256(suffix).hexdigest()))['needData'])
        receiver.write(suffix[:17])
        self.assertEqual(receiver.process.wait(timeout=5), 75)
        self.assertEqual(receiver.process.stdout.read(), b'')
        resumed = self.session()
        self.assertEqual(resumed.ready['bytesCopied'], len(prefix))
        self.assertEqual(resumed.begin(data)['offset'], len(prefix))
        self.assert_ok(resumed.chunk(suffix, len(prefix))[1])
        self.assert_ok(resumed.finish())
        self.assertEqual(self.fs('hash', PARTIAL + '/Data/file'), hashlib.sha256(data).hexdigest())

    def test_lifecycle_progressing_raw_chunk_outlives_idle_limit(self):
        receiver = self.session(code=self.lifecycle_code())
        data = bytes(range(256)) * 6
        self.assert_ok(receiver.begin(data))
        self.assertTrue(receiver.command(dict(op='chunk', path='Data/file', offset=0,
            length=len(data), sha256=hashlib.sha256(data).hexdigest()))['needData'])
        start = time.monotonic()
        for offset in range(0, len(data), 256):
            time.sleep(0.25)
            receiver.write(data[offset:offset + 256])
        self.assertGreater(time.monotonic() - start, 0.6)
        self.assertEqual(self.assert_ok(receiver.read())['offset'], len(data))
        self.assert_ok(receiver.finish())

    def test_lifecycle_read_ahead_preserves_chunk_bytes_and_following_frame(self):
        receiver = self.session()
        data = b'\x00\n{"op":"close"}\xff' * 100
        self.assert_ok(receiver.begin(data))
        offer = dict(op='chunk', path='Data/file', offset=0, length=len(data),
                     sha256=hashlib.sha256(data).hexdigest())
        receiver.write(json.dumps(offer).encode() + b'\n' + data + b'{"op":"status"}\n')
        self.assertTrue(self.assert_ok(receiver.read())['needData'])
        self.assertEqual(self.assert_ok(receiver.read())['offset'], len(data))
        self.assertEqual(self.assert_ok(receiver.read())['bytesCopied'], len(data))
        self.assert_ok(receiver.finish())
        self.assertEqual(self.fs('hash', PARTIAL + '/Data/file'), hashlib.sha256(data).hexdigest())

    def test_lifecycle_active_batch_disk_work_has_no_idle_timer(self):
        code = self.lifecycle_code().replace('function syncFile($handle): void {',
            'function syncFile($handle): void { usleep(1200000);')
        receiver = self.session(code=code)
        start = time.monotonic()
        result = self.assert_ok(receiver.command(dict(op='batch', entries=[self.batch_entry('Data/a', b'durable')])))
        self.assertGreater(time.monotonic() - start, 1.0)
        self.assertTrue(result['batchComplete'])
        self.assertEqual((result['bytesCopied'], result['filesCopied']), (7, 1))
        self.assertTrue(receiver.command(dict(op='heartbeat'))['alive'])

    def test_lifecycle_heartbeats_keep_idle_session_alive(self):
        receiver = self.session(code=self.lifecycle_code())
        for _ in range(5):
            time.sleep(0.25)
            self.assertTrue(self.assert_ok(receiver.command(dict(op='heartbeat')))['alive'])
        blocked = self.session()
        self.assertFalse(blocked.ready['ok'])
        self.assertIn('lock', blocked.ready['error'])

    def test_lifecycle_close_releases_lock_before_ack_without_unlinking(self):
        code = self.lifecycle_code().replace('function reply(array $value): void {',
            "function reply(array $value): void { if ($value['closed'] ?? false) usleep(1200000);")
        receiver = self.session(code=code)
        lock = self.fs('stat', '.portable-export.flock')
        receiver.send(dict(op='close'))
        resumed = self.session()
        self.assert_ok(resumed.ready)
        self.assertIsNone(receiver.process.poll())
        self.assertTrue(self.assert_ok(receiver.read())['closed'])
        self.assertEqual(receiver.process.wait(timeout=5), 0)
        current = self.fs('stat', '.portable-export.flock')
        self.assertEqual((current['dev'], current['ino']), (lock['dev'], lock['ino']))

    def test_lifecycle_blocked_stdout_expires_and_releases_lock(self):
        code = self.lifecycle_code().replace("elseif ($op === 'status') reply(['ok' => true] + counters($db));",
            "elseif ($op === 'status') reply(['ok' => true, 'padding' => str_repeat('x', 32 * 1024 * 1024)]);")
        receiver = self.session(code=code)
        self.assert_ok(receiver.command(dict(op='batch', entries=[self.batch_entry('Data/a', b'saved')])))
        receiver.send(dict(op='status'))
        # Intentionally stop reading its stdout. The SSH channel and local pipe
        # fill, but the receiver must still release its lock after no progress.
        deadline = time.monotonic() + 8
        while True:
            resumed = self.session()
            if resumed.ready.get('ok'):
                break
            resumed.close()
            self.assertLess(time.monotonic(), deadline, resumed.ready)
            time.sleep(0.15)
        self.assertEqual((resumed.ready['bytesCopied'], resumed.ready['filesCopied']), (5, 1))
        receiver.close(kill=True)


if __name__ == "__main__":
    unittest.main()
