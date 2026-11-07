import type {
  ArchiveBackupStatus,
  PortableExportStatus,
  StatusLevel,
  SystemStatus,
  TimeMachineStatus,
} from "@/lib/types";

export function statusColor(level: StatusLevel) {
  if (level === "healthy") return "var(--good)";
  if (level === "critical") return "var(--danger)";
  if (level === "warning") return "var(--warn)";
  return "var(--muted)";
}

export function formatRate(bytesPerSecond: number) {
  const bits = bytesPerSecond * 8;
  if (bits >= 1_000_000_000) return `${(bits / 1_000_000_000).toFixed(2)} Gb/s`;
  if (bits >= 1_000_000) return `${(bits / 1_000_000).toFixed(1)} Mb/s`;
  if (bits >= 1_000) return `${(bits / 1_000).toFixed(0)} Kb/s`;
  return `${bits.toFixed(0)} b/s`;
}

export function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 B";

  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1_000 && unit < units.length - 1) {
    value /= 1_000;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

export function formatPercent(value: number) {
  if (!Number.isFinite(value)) return "—";
  if (value < 1) return `${value.toFixed(2)}%`;
  if (value < 10) return `${value.toFixed(1)}%`;
  return `${value.toFixed(0)}%`;
}

export function formatDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return "Calculating";
  }
  const wholeSeconds = Math.floor(seconds);
  const days = Math.floor(wholeSeconds / 86_400);
  const hours = Math.floor((wholeSeconds % 86_400) / 3_600);
  const minutes = Math.floor((wholeSeconds % 3_600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatAge(date: string | null) {
  if (!date) return "No backup found";
  const deltaMinutes = Math.max(
    0,
    Math.round((Date.now() - new Date(date).getTime()) / 60_000),
  );
  if (deltaMinutes < 2) return "Just now";
  if (deltaMinutes < 60) return `${deltaMinutes} min ago`;
  const hours = Math.floor(deltaMinutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

export function timeMachineDisplay(status: TimeMachineStatus) {
  if (status.backupInProgress) {
    return {
      headline: "Share activity",
      detail: "Recent changes with an open connection",
    };
  }
  if (!status.configured) {
    return { headline: "Not configured", detail: status.message };
  }
  if (!status.exists) {
    return { headline: "Share unavailable", detail: status.message };
  }
  if (!status.latestActivityAt) {
    return { headline: status.status === "healthy" ? "Ready for a new backup" : "No backup data", detail: status.message };
  }
  return {
    headline: status.backupInProgress === null ? "Activity unknown" : "Share idle",
    detail: `Last file activity ${formatAge(status.latestActivityAt)}`,
  };
}

export function formatUptime(seconds: number) {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  if (days > 0) return `${days}d ${hours}h`;
  return hours > 0 ? `${hours}h` : "<1h";
}

export function formatDateTime(date: string | null) {
  if (!date) return "Not copied yet";
  return new Date(date).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function systemLevel(system: SystemStatus): StatusLevel {
  if ((system.cpuTempC ?? 0) >= 75 || system.disk.usedPct >= 90) {
    return "critical";
  }
  if ((system.cpuTempC ?? 0) >= 65 || system.memoryPct >= 90) {
    return "warning";
  }
  return "healthy";
}

export function portableExportContinuation(status: PortableExportStatus) {
  if (status.state !== "paused" || status.errorDetail?.trim()) return null;
  return status.pauseReason === "session-budget" || status.pauseReason === "archive-handoff"
    ? status.pauseReason
    : null;
}

export function portableExportHeadline(status: PortableExportStatus) {
  const continuation = portableExportContinuation(status);
  if (continuation === "session-budget") return "Continuing automatically";
  if (continuation === "archive-handoff") return "Waiting for archive; resumes automatically";
  if (status.state === "paused") return "Paused · progress saved";
  if (status.state === "running") return "Export in progress";
  return formatDateTime(status.lastSuccessAt);
}

export function portableExportSummary(status: PortableExportStatus) {
  if (status.message === "Uploading 1 backed-up files and folders together") {
    return "Uploading a backed-up file or folder";
  }
  const prefixes = ["Uploading backed-up files: ", "Scanning backed-up folders: "];
  for (const prefix of prefixes) {
    if (!status.message.startsWith(prefix)) continue;
    const path = status.message.slice(prefix.length);
    const name = path.split("/").filter(Boolean).at(-1);
    return name ? `${prefix}${name}` : status.message;
  }
  return status.message;
}

export function portableExportLevel(status: PortableExportStatus): StatusLevel {
  if (!status.configured) return "unconfigured";
  if (status.state === "error") return "critical";
  if (portableExportContinuation(status)) return "healthy";
  if (status.state === "requested" || status.state === "running" || status.state === "paused") {
    return "warning";
  }
  return "healthy";
}

export function archiveBackupLevel(status: ArchiveBackupStatus): StatusLevel {
  if (!status.configured) return "unconfigured";
  if (status.state === "error") return "critical";
  if (status.state === "running" || status.state === "maintenance") {
    return "warning";
  }
  return "healthy";
}
