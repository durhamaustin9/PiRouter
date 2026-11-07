import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";
import { GET } from "../app/api/portable-export/route";
import { archiveSourceNeedsCopy, archiveWorkerActive } from "../lib/archiveCoordination";
import { requestPortableExport, updatePortableExport } from "../lib/portableExport";

async function fixture(t: TestContext) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pirouter-coordination-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

test("archive verification, cleanup, and unknown worker states block remounting", () => {
  for (const state of ["active", "activating", "deactivating", "reloading"]) {
    assert.equal(archiveWorkerActive(state), true);
  }
  for (const state of ["inactive", "failed"]) assert.equal(archiveWorkerActive(state), false);
  for (const state of [null, "", "unknown", "not-found"]) assert.equal(archiveWorkerActive(state), null);
});

test("an empty share or unrelated directory does not require a new archive", async (t) => {
  const root = await fixture(t);
  await mkdir(path.join(root, "lost+found"));
  await symlink(path.join(root, "lost+found"), path.join(root, "unrelated.sparsebundle"));
  assert.equal(await archiveSourceNeedsCopy(root, null), false);
  await mkdir(path.join(root, "computer.sparsebundle"));
  assert.equal(await archiveSourceNeedsCopy(root, null), true);
});

test("inaccessible source or failed freshness checks cannot authorize a remount", async (t) => {
  const root = await fixture(t);
  assert.equal(await archiveSourceNeedsCopy(path.join(root, "missing"), null), null);
  await mkdir(path.join(root, "computer.backupbundle"));
  assert.equal(await archiveSourceNeedsCopy(root, "invalid"), null);
  assert.equal(await archiveSourceNeedsCopy(root, "2026-01-01T00:00:00Z", async () => null), null);
});

test("source freshness follows change time, including files restored with older modification dates", async (t) => {
  const root = await fixture(t);
  const bundle = path.join(root, "computer.sparsebundle");
  await mkdir(bundle);
  const completed = "2026-01-01T00:00:00Z";
  let changed = "";
  const probe = async (command: string, args: string[]) => {
    assert.equal(command, "find");
    assert.deepEqual(args, [bundle, "-newerct", "2026-01-01T00:00:00.000Z", "-print", "-quit"]);
    return changed;
  };
  assert.equal(await archiveSourceNeedsCopy(root, completed, probe), false);
  changed = path.join(bundle, "bands/0");
  assert.equal(await archiveSourceNeedsCopy(root, completed, probe), true);
});

test("paired exporter receives archive coordination while uploaded progress stays intact", async (t) => {
  const root = await fixture(t);
  const names = ["PORTABLE_EXPORT_STATUS_PATH", "PORTABLE_EXPORT_TOKEN", "ARCHIVE_STATUS_PATH", "TIME_MACHINE_PATH"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  t.after(() => {
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  });
  process.env.PORTABLE_EXPORT_STATUS_PATH = path.join(root, "portable.json");
  process.env.PORTABLE_EXPORT_TOKEN = "fixture-token";
  process.env.ARCHIVE_STATUS_PATH = path.join(root, "archive.json");
  process.env.TIME_MACHINE_PATH = root;
  await mkdir(path.join(root, "computer.sparsebundle"));
  await writeFile(process.env.ARCHIVE_STATUS_PATH, JSON.stringify({ configured: true, state: "waiting" }));
  assert.equal((await GET(new Request("http://localhost/api/portable-export"))).status, 401);
  const request = await requestPortableExport("automatic");
  await updatePortableExport({ requestId: request.requestId!, state: "paused", message: "Progress saved", bytesCopied: 871 });
  const response = await GET(new Request("http://localhost/api/portable-export", {
    headers: { Authorization: "Bearer fixture-token" },
  }));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.bytesCopied, 871);
  assert.equal(body.requestId, request.requestId);
  assert.equal(body.archiveCoordination.configured, true);
  assert.equal(body.archiveCoordination.state, "waiting");
  assert.equal(body.archiveCoordination.lastSuccessAt, null);
  assert.equal(body.archiveCoordination.needsArchive, process.platform === "linux" ? true : null);
  assert.match(response.headers.get("cache-control")!, /no-store/);
});
