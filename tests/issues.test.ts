import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";
import { dismissIssue, reconcileIssues } from "../lib/status/issues";
import { summarizeIssues, summarizeStatus } from "../lib/status/summary";

async function alertFixture(t: TestContext) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pirouter-alert-recovery-"));
  const previous = process.env.ISSUE_STATUS_PATH;
  const filename = path.join(directory, "issues.json");
  process.env.ISSUE_STATUS_PATH = filename;
  t.after(async () => {
    if (previous === undefined) delete process.env.ISSUE_STATUS_PATH;
    else process.env.ISSUE_STATUS_PATH = previous;
    await rm(directory, { recursive: true, force: true });
  });
  return { directory, filename };
}

test("dismissals persist, expire, and reset on recovery or changed severity", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pirouter-alerts-"));
  process.env.ISSUE_STATUS_PATH = path.join(directory, "issues.json");
  const now = Date.now();
  const issue = { id: "temperature", level: "warning" as const, message: "Warm", scoreImpact: 6 };
  try {
    const first = await reconcileIssues([issue], now);
    await dismissIssue(issue.id, first.issues[0].firstSeenAt!, 24, now);
    assert.equal((await reconcileIssues([issue], now + 1)).issues.length, 0);
    assert.ok(JSON.parse(await readFile(process.env.ISSUE_STATUS_PATH, "utf8")).temperature.dismissedUntil);
    assert.equal(summarizeIssues([]).score, 100);
    assert.equal((await reconcileIssues([issue], now + 24 * 3_600_000)).issues.length, 1);
    await dismissIssue(issue.id, first.issues[0].firstSeenAt!, 24, now);
    const escalated = await reconcileIssues([{ ...issue, level: "critical" }], now + 2);
    assert.equal(escalated.issues.length, 1);
    await assert.rejects(dismissIssue(issue.id, first.issues[0].firstSeenAt!, 24), /changed/);
    await dismissIssue(issue.id, escalated.issues[0].firstSeenAt!, 24, now + 2);
    await reconcileIssues([], now + 3);
    const recurrence = await reconcileIssues([issue], now + 4);
    assert.equal(recurrence.issues.length, 1);
    assert.notEqual(recurrence.issues[0].firstSeenAt, first.issues[0].firstSeenAt);
    await dismissIssue(issue.id, recurrence.issues[0].firstSeenAt!, 1, now + 4);
    await dismissIssue(issue.id, recurrence.issues[0].firstSeenAt!, 0, now + 5);
    assert.equal((await reconcileIssues([issue], now + 6)).issues.length, 1);
    const failure = { ...issue, fingerprint: "mount failed: no destination" };
    const before = await reconcileIssues([failure], now + 7);
    await dismissIssue(issue.id, before.issues[0].firstSeenAt!, 24, now + 7);
    assert.equal((await reconcileIssues([{ ...failure, fingerprint: "mount failed: permission denied" }], now + 8)).issues.length, 1);
  } finally {
    delete process.env.ISSUE_STATUS_PATH;
    await rm(directory, { recursive: true, force: true });
  }
});

test("portable failures are actionable and stale backup alerts cannot hide a missing share", () => {
  const input = {
    network: { online: true }, tailscale: { installed: true, online: true },
    endpoint: { configured: true, reachable: true },
    timeMachine: { configured: true, status: "warning", reason: "stale", message: "Idle" },
    archiveBackup: { configured: true, state: "success" },
    portableExport: { configured: true, state: "error", message: "Mount failed", errorDetail: "No destinations configured" },
    system: { memoryPct: 5, disk: { usedPct: 10 } },
  } as Parameters<typeof summarizeStatus>[0];
  const result = summarizeStatus(input);
  assert.equal(result.summary.message, "2 items need attention");
  assert.equal(result.issues.find((issue) => issue.id === "portable-export")?.detail, "No destinations configured");
  const missing = summarizeStatus({ ...input, timeMachine: { ...input.timeMachine, reason: "missing", status: "critical" } });
  assert.ok(missing.issues.some((issue) => issue.id === "time-machine-missing"));
  assert.ok(!missing.issues.some((issue) => issue.id === "time-machine-stale"));
});

test("invalid alert JSON is preserved and repaired without hiding current failures", async (t) => {
  const { directory, filename } = await alertFixture(t);
  await writeFile(filename, "{interrupted-write");
  t.mock.method(console, "error", () => undefined);
  const issue = { id: "temperature", level: "warning" as const, message: "Warm" };
  const status = await reconcileIssues([issue]);
  assert.equal(status.issues[0].id, issue.id);
  assert.ok(status.issues[0].firstSeenAt);
  const saved = JSON.parse(await readFile(filename, "utf8"));
  assert.equal(saved.temperature.firstSeenAt, status.issues[0].firstSeenAt);
  const backup = (await readdir(directory)).find((name) => name.startsWith("issues.json.corrupt-"));
  assert.ok(backup);
  assert.equal(await readFile(path.join(directory, backup), "utf8"), "{interrupted-write");
  await dismissIssue(issue.id, status.issues[0].firstSeenAt!, 1);
  assert.equal((await reconcileIssues([issue])).issues.length, 0);
});

test("malformed saved occurrences regain valid identifiers for alert actions", async (t) => {
  const { filename } = await alertFixture(t);
  const issue = { id: "temperature", level: "warning" as const, message: "Warm" };
  await writeFile(filename, JSON.stringify({ temperature: {
    signature: JSON.stringify([issue.level, issue.message]), firstSeenAt: null,
    dismissedUntil: "not-a-date",
  } }));
  const status = await reconcileIssues([issue]);
  assert.equal(status.issues.length, 1);
  assert.equal(typeof status.issues[0].firstSeenAt, "string");
  await dismissIssue(issue.id, status.issues[0].firstSeenAt!, 1);
  assert.equal((await reconcileIssues([issue])).issues.length, 0);
});

test("a failed alert write keeps health data visible and recovers on the next successful write", async (t) => {
  const { filename } = await alertFixture(t);
  t.mock.method(console, "error", () => undefined);
  const write = t.mock.method(fs, "writeFile", async () => {
    throw Object.assign(new Error("Read-only filesystem"), { code: "EROFS" });
  });
  const issue = { id: "temperature", level: "warning" as const, message: "Warm" };
  const failed = await reconcileIssues([issue]);
  assert.deepEqual(failed.issues.map((entry) => entry.id), ["temperature", "alert-history"]);
  write.mock.restore();
  const recovered = await reconcileIssues([issue]);
  assert.equal(recovered.issues.length, 1);
  assert.equal(recovered.issues[0].id, issue.id);
  assert.ok(JSON.parse(await readFile(filename, "utf8")).temperature.firstSeenAt);
});
