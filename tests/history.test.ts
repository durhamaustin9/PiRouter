import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

test("invalid and fractional history limits retain usable history instead of failing SQLite queries", async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "pirouter-history-"));
  const previous = process.env.DATABASE_URL;
  process.env.DATABASE_URL = `file:${path.join(directory, "history.db")}`;
  const { ensureDatabase, prisma } = await import("../lib/prisma");
  const { getHistory } = await import("../lib/history");
  t.after(async () => {
    await prisma.$disconnect();
    if (previous === undefined) delete process.env.DATABASE_URL;
    else process.env.DATABASE_URL = previous;
    await rm(directory, { recursive: true, force: true });
  });
  await ensureDatabase();
  await prisma.healthSnapshot.createMany({
    data: [1, 2, 3].map((n) => ({
      capturedAt: new Date(n * 60_000),
      downloadBps: n,
      uploadBps: n,
      cpuLoadPct: 1,
      memoryPct: 10,
      diskPct: 20,
      tailscaleUp: true,
    })),
  });

  for (const limit of [NaN, Infinity, -Infinity]) {
    assert.deepEqual((await getHistory(limit)).map((point) => point.uploadBps), [1, 2, 3]);
  }
  assert.deepEqual((await getHistory(1.9)).map((point) => point.uploadBps), [3]);
  assert.deepEqual((await getHistory(0)).map((point) => point.uploadBps), [3]);
});
