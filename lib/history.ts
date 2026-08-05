import { ensureDatabase, prisma } from "@/lib/prisma";
import type { HistoryPoint, PiStatus } from "@/lib/types";

let lastRecordedAt = 0;
let lastPrunedAt = 0;

const SAMPLE_INTERVAL_MS = 60_000;
const PRUNE_INTERVAL_MS = 6 * 60 * 60_000;
const RETENTION_MS = 7 * 24 * 60 * 60_000;

export async function recordSnapshot(status: PiStatus) {
  const now = Date.now();
  if (now - lastRecordedAt < SAMPLE_INTERVAL_MS) return;
  lastRecordedAt = now;

  try {
    await ensureDatabase();
    await prisma.healthSnapshot.create({
      data: {
        downloadBps: status.network.downloadBps,
        uploadBps: status.network.uploadBps,
        latencyMs: status.network.latencyMs,
        packetLossPct: status.network.packetLossPct,
        cpuTempC: status.system.cpuTempC,
        cpuLoadPct: status.system.cpuLoadPct,
        memoryPct: status.system.memoryPct,
        diskPct: status.system.disk.usedPct,
        tailscaleUp: status.tailscale.online,
        endpointReachable: status.endpoint.reachable,
        timeMachineOk: status.timeMachine.configured
          ? status.timeMachine.status === "healthy"
          : null,
      },
    });

    if (now - lastPrunedAt >= PRUNE_INTERVAL_MS) {
      await prisma.healthSnapshot.deleteMany({
        where: {
          capturedAt: { lt: new Date(now - RETENTION_MS) },
        },
      });
      lastPrunedAt = now;
    }
  } catch (error) {
    lastRecordedAt = 0;
    console.error("Unable to record health snapshot", error);
  }
}

export async function getHistory(limit = 90): Promise<HistoryPoint[]> {
  await ensureDatabase();
  const rows = await prisma.healthSnapshot.findMany({
    orderBy: { capturedAt: "desc" },
    take: Math.min(360, Math.max(1, limit)),
  });

  return rows.reverse().map((row) => ({
    capturedAt: row.capturedAt.toISOString(),
    downloadBps: row.downloadBps,
    uploadBps: row.uploadBps,
    latencyMs: row.latencyMs,
    cpuTempC: row.cpuTempC,
    cpuLoadPct: row.cpuLoadPct,
    memoryPct: row.memoryPct,
    diskPct: row.diskPct,
  }));
}
