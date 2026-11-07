import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";
import { PATCH, POST } from "../app/api/portable-export/route";
import {
  readPortableExportStatus,
  requestPortableExport,
  updatePortableExport,
} from "../lib/portableExport";
import {
  portableExportContinuation,
  portableExportHeadline,
  portableExportLevel,
} from "../components/dashboard/formatters";

async function useStatusFile(t: TestContext) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pirouter-export-"));
  const previous = {
    PORTABLE_EXPORT_STATUS_PATH: process.env.PORTABLE_EXPORT_STATUS_PATH,
    PORTABLE_EXPORT_TOKEN: process.env.PORTABLE_EXPORT_TOKEN,
  };
  const filename = path.join(directory, "status.json");
  process.env.PORTABLE_EXPORT_STATUS_PATH = filename;
  process.env.PORTABLE_EXPORT_TOKEN = "test-export-token";
  t.after(async () => {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    await rm(directory, { recursive: true, force: true });
  });
  return filename;
}

function patchRequest(body: unknown) {
  return new Request("http://localhost/api/portable-export", {
    method: "PATCH",
    headers: {
      Authorization: "Bearer test-export-token",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

test("disconnected exporters become paused without losing uploaded progress", async (t) => {
  const filename = await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  const running = await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Copying files",
    activeSnapshotName: "2000-01-01-000000",
    bytesCopied: 1_234_567,
    filesCopied: 120,
  });
  const stale = {
    ...running,
    lastHeartbeatAt: new Date(Date.now() - 180_000).toISOString(),
  };
  await writeFile(filename, JSON.stringify(stale));

  const paused = await readPortableExportStatus();
  assert.equal(paused.state, "paused");
  assert.equal(paused.message, "Waiting for the Mac exporter; uploaded work is retained");
  assert.equal(paused.requestId, requested.requestId);
  assert.equal(paused.requestedAt, requested.requestedAt);
  assert.equal(paused.activeSnapshotName, running.activeSnapshotName);
  assert.equal(paused.bytesCopied, running.bytesCopied);
  assert.equal(paused.filesCopied, running.filesCopied);
  assert.equal(paused.pauseReason, null);
  assert.equal(portableExportContinuation(paused), null);
  assert.equal(portableExportLevel(paused), "warning");
  assert.equal(JSON.parse(await readFile(filename, "utf8")).state, "running");

  const response = await POST(new Request("http://localhost/api/portable-export", {
    method: "POST",
  }));
  assert.equal(response.status, 202);
  const { status: resumed } = await response.json();
  assert.equal(resumed.state, "requested");
  assert.equal(resumed.requestId, requested.requestId);
  assert.equal(resumed.requestedAt, requested.requestedAt);
  assert.equal(resumed.activeSnapshotName, running.activeSnapshotName);
  assert.equal(resumed.bytesCopied, running.bytesCopied);
  assert.equal(resumed.filesCopied, running.filesCopied);
});

test("legacy session limits are scheduled continuations without rewriting saved work", async (t) => {
  const filename = await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  const detail = "This export session has ended; the next session will continue";
  const saved = {
    ...requested,
    state: "paused",
    message: `Export paused; saved work is retained. ${detail}`,
    errorDetail: detail,
    activeSnapshotName: "2000-01-01-000000",
    bytesCopied: 22_938_200_710,
    filesCopied: 77_082,
  };
  await writeFile(filename, JSON.stringify(saved));
  const status = await readPortableExportStatus();
  assert.equal(status.state, "paused");
  assert.equal(status.pauseReason, "session-budget");
  assert.equal(status.message, "Continuing automatically; saved work is retained");
  assert.equal(status.errorDetail, null);
  assert.equal(status.requestId, requested.requestId);
  assert.equal(status.requestedAt, requested.requestedAt);
  assert.equal(status.activeSnapshotName, saved.activeSnapshotName);
  assert.equal(status.bytesCopied, saved.bytesCopied);
  assert.equal(status.filesCopied, saved.filesCopied);
  assert.equal(portableExportHeadline(status), "Continuing automatically");
  assert.equal(portableExportContinuation(status), "session-budget");
  assert.equal(portableExportLevel(status), "healthy");
  assert.equal(JSON.parse(await readFile(filename, "utf8")).errorDetail, detail);
});

test("explicit scheduled continuation clears an earlier failure and preserves the request", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  await updatePortableExport({
    requestId: requested.requestId!, state: "paused", message: "Connection lost",
    errorDetail: "Unraid connection ended", bytesCopied: 300, filesCopied: 2,
  });
  const response = await PATCH(patchRequest({
    requestId: requested.requestId,
    state: "paused",
    pauseReason: "session-budget",
    message: "The next scheduled session will continue",
  }));
  assert.equal(response.status, 200);
  const status = await response.json();
  assert.equal(status.pauseReason, "session-budget");
  assert.equal(status.message, "Continuing automatically; saved work is retained");
  assert.equal(status.errorDetail, null);
  assert.equal(status.requestId, requested.requestId);
  assert.equal(status.bytesCopied, 300);
  assert.equal(status.filesCopied, 2);
  assert.equal(portableExportLevel(status), "healthy");
  const running = await updatePortableExport({
    requestId: requested.requestId!, state: "running", message: "Copying files",
  });
  assert.equal(running.pauseReason, null);
  assert.equal(portableExportLevel(running), "warning");
});

test("known archive handoffs show automatic waiting while archive failures remain visible", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  for (const message of [
    "Waiting for Pi archive status; folder progress is saved",
    "Pi is archiving; folder progress is saved",
    "Waiting for the Pi archive check; folder progress is saved",
    "Waiting for the Pi archive window; folder progress is saved",
    "Waiting to close the idle backup image before archiving",
  ]) {
    const status = await updatePortableExport({
      requestId: requested.requestId!, state: "paused", message,
      bytesCopied: 500, filesCopied: 3,
    });
    assert.equal(status.pauseReason, "archive-handoff", message);
    assert.equal(status.message, "Waiting for archive; resumes automatically");
    assert.equal(portableExportHeadline(status), status.message);
    assert.equal(portableExportLevel(status), "healthy");
    assert.equal(status.errorDetail, null);
    assert.equal(status.requestId, requested.requestId);
    assert.equal(status.bytesCopied, 500);
  }
  const failed = await updatePortableExport({
    requestId: requested.requestId!, state: "paused", pauseReason: "archive-handoff",
    message: "Waiting for the Pi archive check; folder progress is saved",
    errorDetail: "The archive repository is unavailable",
  });
  assert.equal(failed.pauseReason, null);
  assert.equal(failed.errorDetail, "The archive repository is unavailable");
  assert.equal(portableExportLevel(failed), "warning");
});

test("real pauses and errors cannot be hidden by a continuation reason or similar wording", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  for (const errorDetail of [
    "Another export currently holds the destination lock",
    "Written chunk SHA-256 mismatch",
    "Unraid connection ended",
    "This export session has ended; the next session will continue after a data failure",
  ]) {
    const status = await updatePortableExport({
      requestId: requested.requestId!, state: "paused", pauseReason: "session-budget",
      message: "Export paused; saved work is retained", errorDetail,
    });
    assert.equal(status.pauseReason, null);
    assert.equal(status.errorDetail, errorDetail);
    assert.equal(portableExportContinuation(status), null);
    assert.equal(portableExportHeadline(status), "Paused · progress saved");
    assert.equal(portableExportLevel(status), "warning");
  }
  const unknown = await updatePortableExport({
    requestId: requested.requestId!, state: "paused",
    message: "Export paused; uploaded work is retained and will resume automatically",
  });
  assert.equal(unknown.pauseReason, null);
  assert.equal(portableExportLevel(unknown), "warning");
  const failed = await updatePortableExport({
    requestId: requested.requestId!, state: "error", pauseReason: "session-budget",
    message: "Continuing automatically; saved work is retained",
    errorDetail: "Could not read the source file",
  });
  assert.equal(failed.pauseReason, null);
  assert.equal(failed.errorDetail, "Could not read the source file");
  assert.equal(portableExportLevel(failed), "critical");
});

test("legacy running statuses expire using their attempt time, while heartbeats renew running work", async (t) => {
  const filename = await useStatusFile(t);
  const requested = await requestPortableExport("automatic");
  const legacy = {
    ...requested,
    state: "running",
    lastAttemptAt: new Date(Date.now() - 180_000).toISOString(),
  };
  delete legacy.lastHeartbeatAt;
  await writeFile(filename, JSON.stringify(legacy));
  assert.equal((await readPortableExportStatus()).state, "paused");

  const before = Date.now();
  const running = await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Copy resumed",
    bytesCopied: 400,
    filesCopied: 3,
    activeSnapshotName: "2000-01-01-000000",
  });
  assert.ok(Date.parse(running.lastHeartbeatAt!) >= before);
  assert.equal((await readPortableExportStatus()).state, "running");
  assert.equal((await requestPortableExport("manual")).requestId, requested.requestId);

  await writeFile(filename, JSON.stringify({ ...running, lastAttemptAt: legacy.lastAttemptAt }));
  assert.equal((await readPortableExportStatus()).state, "running");
  const unchangedProgress = await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Still copying",
  });
  assert.equal(unchangedProgress.bytesCopied, 400);
  assert.equal(unchangedProgress.filesCopied, 3);
  assert.equal(unchangedProgress.activeSnapshotName, running.activeSnapshotName);
});

test("explicit pauses and retryable failures retain the request and accept zero progress", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  const running = await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Working",
    bytesCopied: 200,
    filesCopied: 4,
  });
  const pausedResponse = await PATCH(patchRequest({
    requestId: requested.requestId,
    state: "paused",
    message: "Waiting for connection",
    bytesCopied: null,
  }));
  assert.equal(pausedResponse.status, 200);
  const paused = await pausedResponse.json();
  assert.equal(paused.requestId, requested.requestId);
  assert.equal(paused.bytesCopied, running.bytesCopied);
  assert.equal(paused.filesCopied, running.filesCopied);

  const error = await updatePortableExport({
    requestId: requested.requestId!,
    state: "error",
    message: "Connection lost",
    errorDetail: "Retry later",
  });
  assert.equal(error.requestId, requested.requestId);
  assert.equal(error.requestedAt, requested.requestedAt);
  const resumed = await requestPortableExport("automatic");
  assert.equal(resumed.requestId, requested.requestId);
  assert.equal(resumed.bytesCopied, running.bytesCopied);
  const reset = await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Measuring progress",
    bytesCopied: 0,
    filesCopied: 0,
  });
  assert.equal(reset.bytesCopied, 0);
  assert.equal(reset.filesCopied, 0);
});

test("only verified success finishes a request and preserves its completed snapshot", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  await updatePortableExport({
    requestId: requested.requestId!,
    state: "running",
    message: "Verifying",
    activeSnapshotName: "2000-01-01-000000",
    bytesCopied: 500,
    filesCopied: 5,
  });
  await assert.rejects(updatePortableExport({
    requestId: requested.requestId!,
    state: "success",
    message: "Done",
  }), /must be verified/);
  assert.equal((await readPortableExportStatus()).requestId, requested.requestId);
  assert.equal((await readPortableExportStatus()).lastSuccessAt, null);

  const success = await updatePortableExport({
    requestId: requested.requestId!,
    state: "success",
    message: "Verified export ready",
    verified: true,
  });
  assert.equal(success.state, "success");
  assert.equal(success.verified, true);
  assert.equal(success.requestId, null);
  assert.equal(success.requestedAt, null);
  assert.equal(success.activeSnapshotName, null);
  assert.equal(success.lastSnapshotName, "2000-01-01-000000");
  assert.equal(success.bytesCopied, 500);
  assert.equal(success.filesCopied, 5);
  assert.ok(success.lastSuccessAt);
});

test("the exporter API rejects invalid progress and unverified completion", async (t) => {
  await useStatusFile(t);
  const requested = await requestPortableExport("manual");
  const body = { requestId: requested.requestId, state: "running", message: "Working" };
  for (const invalid of [
    { ...body, bytesCopied: -1 },
    { ...body, filesCopied: -1 },
    { ...body, bytesCopied: "100" },
    { ...body, filesCopied: "1" },
    { ...body, pauseReason: "connection-error" },
    { ...body, pauseReason: 1 },
    { ...body, state: "success", verified: false },
    null,
    [],
  ]) {
    assert.equal((await PATCH(patchRequest(invalid))).status, 400);
  }
  const infinity = patchRequest(body);
  const infinityRequest = new Request(infinity.url, {
    method: "PATCH",
    headers: infinity.headers,
    body: JSON.stringify({ ...body, bytesCopied: "infinity" })
      .replace('"bytesCopied":"infinity"', '"bytesCopied":1e999'),
  });
  assert.equal((await PATCH(infinityRequest)).status, 400);
  for (const bytesCopied of [NaN, Infinity, -1]) {
    await assert.rejects(updatePortableExport({
      requestId: requested.requestId!,
      state: "running",
      message: "Working",
      bytesCopied,
    }), /finite nonnegative/);
  }
  assert.equal((await readPortableExportStatus()).state, "requested");
});
