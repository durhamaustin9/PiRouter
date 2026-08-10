import type { PiStatus } from "@/lib/types";
import { collectNetworkStatus } from "@/lib/status/network";
import {
  collectArchiveBackupStatus,
  collectEndpointStatus,
  collectPortableExportStatus,
  collectTailscaleStatus,
  collectTimeMachineStatus,
} from "@/lib/status/services";
import { summarizeStatus } from "@/lib/status/summary";
import { collectSystemStatus } from "@/lib/status/system";

export {
  getSpeedTestJobStatus,
  startSpeedTestJob,
} from "@/lib/status/speedtest";

let collectionInFlight: Promise<PiStatus> | null = null;

async function collect(): Promise<PiStatus> {
  const healthTarget = process.env.HEALTH_TARGET?.trim() || "1.1.1.1";
  const diskPath = process.env.TIME_MACHINE_PATH?.trim() || "/";
  const [
    network,
    tailscale,
    endpoint,
    timeMachine,
    archiveBackup,
    portableExport,
    system,
  ] =
    await Promise.all([
      collectNetworkStatus(healthTarget),
      collectTailscaleStatus(),
      collectEndpointStatus(),
      collectTimeMachineStatus(),
      collectArchiveBackupStatus(),
      collectPortableExportStatus(),
      collectSystemStatus(diskPath),
    ]);
  const { issues, summary } = summarizeStatus({
    network,
    tailscale,
    endpoint,
    timeMachine,
    archiveBackup,
    system,
  });

  return {
    capturedAt: new Date().toISOString(),
    identity: {
      name: process.env.DASHBOARD_NAME?.trim() || "PiRouter",
      location: process.env.LOCATION_LABEL?.trim() || "Home network",
    },
    summary,
    network,
    tailscale,
    endpoint,
    timeMachine,
    archiveBackup,
    portableExport,
    system,
    issues,
  };
}

/** A second browser cannot start the same collection work twice. */
export function collectPiStatus(): Promise<PiStatus> {
  if (collectionInFlight) return collectionInFlight;
  collectionInFlight = collect().finally(() => {
    collectionInFlight = null;
  });
  return collectionInFlight;
}
