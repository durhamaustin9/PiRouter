import { promises as fs } from "node:fs";
import net from "node:net";
import path from "node:path";
import type {
  EndpointStatus,
  TailscaleStatus,
  TimeMachineStatus,
} from "@/lib/types";
import { readPortableExportStatus } from "@/lib/portableExport";
import { createCachedProbe } from "@/lib/status/cache";
import { round, runCommand } from "@/lib/status/shared";

const getCachedTailscale = createCachedProbe<TailscaleStatus>(30_000);
const getCachedEndpoint = createCachedProbe<EndpointStatus>(15_000);
const getCachedTimeMachine = createCachedProbe<TimeMachineStatus>(60_000);

export async function collectTailscaleStatus(): Promise<TailscaleStatus> {
  return getCachedTailscale(async () => {
    const output = await runCommand("tailscale", ["status", "--json"], 5_000);
    if (!output) {
      return {
        installed: false,
        online: false,
        backendState: "Unavailable",
        ip: null,
        peerCount: 0,
        onlinePeerCount: 0,
        directPeerCount: 0,
        hostname: null,
      };
    }

    try {
      const data = JSON.parse(output) as {
        BackendState?: string;
        TailscaleIPs?: string[];
        Self?: { Online?: boolean; HostName?: string; DNSName?: string };
        Peer?: Record<
          string,
          { Online?: boolean; CurAddr?: string }
        >;
      };
      const peers = Object.values(data.Peer ?? {});
      return {
        installed: true,
        online:
          data.BackendState === "Running" && data.Self?.Online !== false,
        backendState: data.BackendState ?? "Unknown",
        ip: data.TailscaleIPs?.[0] ?? null,
        peerCount: peers.length,
        onlinePeerCount: peers.filter((peer) => peer.Online).length,
        directPeerCount: peers.filter(
          (peer) => peer.Online && Boolean(peer.CurAddr),
        ).length,
        hostname: data.Self?.HostName ?? data.Self?.DNSName ?? null,
      };
    } catch {
      return {
        installed: true,
        online: false,
        backendState: "Invalid response",
        ip: null,
        peerCount: 0,
        onlinePeerCount: 0,
        directPeerCount: 0,
        hostname: null,
      };
    }
  });
}

function probeTcp(host: string, port: number) {
  return new Promise<{ reachable: boolean; latencyMs: number | null }>(
    (resolve) => {
      const startedAt = performance.now();
      const socket = net.createConnection({ host, port });
      let finished = false;

      const finish = (reachable: boolean) => {
        if (finished) return;
        finished = true;
        socket.destroy();
        resolve({
          reachable,
          latencyMs: reachable ? round(performance.now() - startedAt) : null,
        });
      };

      socket.setTimeout(3_000);
      socket.once("connect", () => finish(true));
      socket.once("timeout", () => finish(false));
      socket.once("error", () => finish(false));
    },
  );
}

export async function collectEndpointStatus(): Promise<EndpointStatus> {
  return getCachedEndpoint(async () => {
    const label = process.env.ENDPOINT_LABEL?.trim() || "Service endpoint";
    const host = process.env.ENDPOINT_HOST?.trim() || null;
    const parsedPort = Number.parseInt(
      process.env.ENDPOINT_PORT || "443",
      10,
    );
    const port = Number.isFinite(parsedPort) ? parsedPort : 443;
    if (!host) {
      return {
        configured: false,
        label,
        host,
        port,
        reachable: null,
        latencyMs: null,
        message: "Add ENDPOINT_HOST to start route checks",
      };
    }

    const result = await probeTcp(host, port);
    return {
      configured: true,
      label,
      host,
      port,
      ...result,
      message: result.reachable
        ? `${label} is reachable over TCP`
        : `${label} did not respond`,
    };
  });
}

async function latestModified(
  target: string,
  maxDepth = 3,
  budget = { remaining: 600 },
): Promise<number | null> {
  if (maxDepth < 0 || budget.remaining <= 0) return null;

  let entries;
  try {
    entries = await fs.readdir(/* turbopackIgnore: true */ target, {
      withFileTypes: true,
    });
  } catch {
    return null;
  }

  let latest: number | null = null;
  for (const entry of entries) {
    if (budget.remaining-- <= 0) break;
    const child = path.join(/* turbopackIgnore: true */ target, entry.name);
    try {
      const stat = await fs.stat(/* turbopackIgnore: true */ child);
      latest = Math.max(latest ?? 0, stat.mtimeMs);
      if (entry.isDirectory() && entry.name !== "bands") {
        const nested = await latestModified(child, maxDepth - 1, budget);
        if (nested) latest = Math.max(latest ?? 0, nested);
      }
    } catch {
      // A backup can change while it is inspected; vanished entries are safe to skip.
    }
  }
  return latest;
}

async function serviceActive(service: string) {
  if (process.platform !== "linux") return null;
  return (await runCommand("systemctl", ["is-active", service], 3_000)) === "active";
}

async function timeMachineBackupInProgress(
  target: string,
): Promise<boolean | null> {
  if (process.platform !== "linux") return null;
  const output = await runCommand(
    "sudo",
    ["-n", "/usr/bin/smbstatus", "--locks", "--json"],
    5_000,
  );
  if (!output) return null;

  try {
    const data = JSON.parse(output) as { open_files?: Record<string, unknown> };
    const prefix = `${target}${path.sep}`;
    return Object.keys(data.open_files ?? {}).some(
      (openPath) => openPath === target || openPath.startsWith(prefix),
    );
  } catch {
    return null;
  }
}

export async function collectTimeMachineStatus(): Promise<TimeMachineStatus> {
  return getCachedTimeMachine(async () => {
    const target = process.env.TIME_MACHINE_PATH?.trim() || null;
    const staleAfterHours = Number.parseFloat(
      process.env.TIME_MACHINE_STALE_HOURS || "36",
    );
    const service = process.env.TIME_MACHINE_SERVICE?.trim() || "smbd";
    const active = await serviceActive(service);

    if (!target) {
      return {
        configured: false,
        path: target,
        exists: false,
        serviceActive: active,
        backupInProgress: null,
        latestActivityAt: null,
        ageHours: null,
        staleAfterHours,
        status: "unconfigured",
        message: "Add the Time Machine share path",
      };
    }

    let exists = false;
    try {
      exists = (
        await fs.stat(/* turbopackIgnore: true */ target)
      ).isDirectory();
    } catch {
      exists = false;
    }

    const [latest, shareHasOpenFiles] = exists
      ? await Promise.all([
          latestModified(target),
          timeMachineBackupInProgress(target),
        ])
      : [null, null];
    const configuredActiveWindow = Number.parseFloat(
      process.env.TIME_MACHINE_ACTIVE_WINDOW_MINUTES || "3",
    );
    const activeWindowMinutes =
      Number.isFinite(configuredActiveWindow) && configuredActiveWindow > 0
        ? configuredActiveWindow
        : 3;
    const backupInProgress =
      shareHasOpenFiles === null
        ? null
        : shareHasOpenFiles &&
          latest !== null &&
          Date.now() - latest <= activeWindowMinutes * 60_000;
    const ageHours = latest ? (Date.now() - latest) / 3_600_000 : null;
    const healthy =
      exists &&
      active !== false &&
      (backupInProgress === true ||
        (ageHours !== null && ageHours <= staleAfterHours));
    const status = healthy
      ? "healthy"
      : exists && ageHours !== null
        ? "warning"
        : "critical";
    let message = "Recent file activity detected";
    if (backupInProgress === true) {
      message = "Time Machine is writing to the backup share";
    } else if (!exists) {
      message = "Backup path is unavailable";
    } else if (ageHours === null) {
      message = "No Time Machine data found";
    } else if (ageHours > staleAfterHours) {
      message = `No file activity within ${staleAfterHours} hours`;
    } else if (backupInProgress === false) {
      message = "Share is idle · recent file activity detected";
    }

    return {
      configured: true,
      path: target,
      exists,
      serviceActive: active,
      backupInProgress,
      latestActivityAt: latest ? new Date(latest).toISOString() : null,
      ageHours: ageHours === null ? null : round(ageHours),
      staleAfterHours,
      status,
      message,
    };
  });
}

export const collectPortableExportStatus = readPortableExportStatus;
