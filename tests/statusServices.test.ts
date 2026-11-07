import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { collectEndpointStatus, collectTimeMachineStatus } from "../lib/status/services";

test("an out-of-range endpoint port cannot crash status collection", async (t) => {
  const previous = { host: process.env.ENDPOINT_HOST, port: process.env.ENDPOINT_PORT };
  process.env.ENDPOINT_HOST = "127.0.0.1";
  process.env.ENDPOINT_PORT = "65536";
  t.after(() => {
    if (previous.host === undefined) delete process.env.ENDPOINT_HOST;
    else process.env.ENDPOINT_HOST = previous.host;
    if (previous.port === undefined) delete process.env.ENDPOINT_PORT;
    else process.env.ENDPOINT_PORT = previous.port;
  });

  const status = await collectEndpointStatus();
  assert.equal(status.configured, true);
  assert.equal(status.port, 443);
  assert.equal(typeof status.reachable, "boolean");
});

test("an unreadable share remains a critical storage alert without failing other telemetry", async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "pirouter-status-share-"));
  const names = ["TIME_MACHINE_PATH", "TIME_MACHINE_STALE_HOURS", "ARCHIVE_STATUS_PATH"];
  const previous = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  process.env.TIME_MACHINE_PATH = directory;
  process.env.TIME_MACHINE_STALE_HOURS = "not-a-duration";
  process.env.ARCHIVE_STATUS_PATH = path.join(directory, "archive.json");
  await fs.writeFile(process.env.ARCHIVE_STATUS_PATH, JSON.stringify({
    configured: true,
    state: "success",
    lastCleanupAt: new Date().toISOString(),
  }));
  t.after(async () => {
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
    await fs.rm(directory, { recursive: true, force: true });
  });
  t.mock.method(fs, "readdir", async () => {
    throw Object.assign(new Error("Access denied while listing share"), { code: "EACCES" });
  });

  const status = await collectTimeMachineStatus();
  assert.equal(status.exists, true);
  assert.equal(status.status, "critical");
  assert.equal(status.reason, "unreadable");
  assert.equal(status.message, "Backup path cannot be read");
  assert.equal(status.backupInProgress, null);
  assert.equal(status.staleAfterHours, 36);
});
