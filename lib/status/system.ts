import { promises as fs } from "node:fs";
import os from "node:os";
import type { DiskStatus, SystemStatus } from "@/lib/types";
import { createCachedProbe } from "@/lib/status/cache";
import { readNumber, round, runCommand } from "@/lib/status/shared";

const getCachedDisk = createCachedProbe<DiskStatus>(60_000);

async function collectDiskStatus(target: string): Promise<DiskStatus> {
  return getCachedDisk(async () => {
    const output = await runCommand("df", ["-kP", target]);
    const line = output?.split("\n").at(-1)?.trim();
    const fields = line?.split(/\s+/) ?? [];
    if (fields.length >= 6) {
      const totalBytes = Number.parseInt(fields[1], 10) * 1024;
      const usedBytes = Number.parseInt(fields[2], 10) * 1024;
      return {
        path: target,
        filesystem: fields[0],
        mount: fields.slice(5).join(" "),
        usedBytes,
        totalBytes,
        usedPct: totalBytes ? round((usedBytes / totalBytes) * 100) : 0,
      };
    }

    return {
      path: target,
      filesystem: "Unknown",
      mount: target,
      usedBytes: 0,
      totalBytes: 0,
      usedPct: 0,
    };
  });
}

async function cpuTemperature() {
  const raw = await readNumber("/sys/class/thermal/thermal_zone0/temp");
  return raw === null ? null : round(raw > 1_000 ? raw / 1_000 : raw);
}

async function memoryStatus() {
  if (process.platform === "linux") {
    try {
      const meminfo = await fs.readFile(
        /* turbopackIgnore: true */ "/proc/meminfo",
        "utf8",
      );
      const totalKb = Number.parseInt(
        meminfo.match(/^MemTotal:\s+(\d+)/m)?.[1] ?? "0",
        10,
      );
      const availableKb = Number.parseInt(
        meminfo.match(/^MemAvailable:\s+(\d+)/m)?.[1] ?? "0",
        10,
      );
      if (totalKb > 0 && availableKb >= 0) {
        const totalBytes = totalKb * 1024;
        const usedBytes = (totalKb - availableKb) * 1024;
        return {
          totalBytes,
          usedBytes,
          usedPct: round((usedBytes / totalBytes) * 100),
        };
      }
    } catch {
      // Node's portable memory values remain a safe fallback.
    }
  }

  const totalBytes = os.totalmem();
  const usedBytes = totalBytes - os.freemem();
  return {
    totalBytes,
    usedBytes,
    usedPct: round((usedBytes / totalBytes) * 100),
  };
}

export async function collectSystemStatus(
  diskPath: string,
): Promise<SystemStatus> {
  const [disk, cpuTempC, memory] = await Promise.all([
    collectDiskStatus(diskPath),
    cpuTemperature(),
    memoryStatus(),
  ]);

  return {
    hostname: os.hostname(),
    uptimeSeconds: os.uptime(),
    cpuLoadPct: round(
      Math.min(100, (os.loadavg()[0] / Math.max(1, os.cpus().length)) * 100),
    ),
    cpuTempC,
    memoryUsedBytes: memory.usedBytes,
    memoryTotalBytes: memory.totalBytes,
    memoryPct: memory.usedPct,
    disk,
  };
}
