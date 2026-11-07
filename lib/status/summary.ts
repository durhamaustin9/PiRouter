import type {
  ArchiveBackupStatus,
  EndpointStatus,
  NetworkStatus,
  PortableExportStatus,
  StatusIssue,
  StatusSummary,
  SystemStatus,
  TailscaleStatus,
  TimeMachineStatus,
} from "@/lib/types";

type SummaryInput = {
  network: NetworkStatus;
  tailscale: TailscaleStatus;
  endpoint: EndpointStatus;
  timeMachine: TimeMachineStatus;
  archiveBackup: ArchiveBackupStatus;
  portableExport: PortableExportStatus;
  system: SystemStatus;
};

export function summarizeStatus({
  network,
  tailscale,
  endpoint,
  timeMachine,
  archiveBackup,
  portableExport,
  system,
}: SummaryInput): { issues: StatusIssue[]; summary: StatusSummary } {
  const issues: StatusIssue[] = [];

  if (!network.online) {
    issues.push({
      id: "network",
      level: "critical",
      message: "Internet health check failed",
      detail: `Health target: ${network.healthTarget}. DNS: ${network.dnsOk ? "responding" : "failed"}. Packet loss: ${network.packetLossPct ?? "unknown"}%.`,
    });
  } else if (
    (network.packetLossPct ?? 0) > 2 ||
    (network.latencyMs ?? 0) > 100
  ) {
    issues.push({
      id: "latency",
      level: "warning",
      message: "Network quality is degraded",
      detail: `Latency: ${network.latencyMs ?? "unknown"} ms (warning above 100 ms). Packet loss: ${network.packetLossPct ?? "unknown"}% (warning above 2%).`,
    });
  }

  if (!tailscale.installed) {
    issues.push({
      id: "tailscale",
      level: "info",
      message: "Tailscale is not installed",
    });
  } else if (!tailscale.online) {
    issues.push({
      id: "tailscale",
      level: "critical",
      message: "Tailscale is offline",
      detail: `Tailscale backend state: ${tailscale.backendState}.`,
    });
  }

  if (!endpoint.configured) {
    issues.push({
      id: "endpoint",
      level: "info",
      message: "Service endpoint is not configured",
    });
  } else if (!endpoint.reachable) {
    issues.push({
      id: "endpoint",
      level: "critical",
      message: `${endpoint.label} is unreachable`,
      detail: `TCP target: ${endpoint.host}:${endpoint.port}. ${endpoint.message}.`,
    });
  }

  if (!timeMachine.configured) {
    issues.push({
      id: "time-machine",
      level: "info",
      message: "Time Machine path is not configured",
    });
  } else if (timeMachine.status !== "healthy") {
    issues.push({
      id: `time-machine-${timeMachine.reason ?? timeMachine.status}`,
      level: timeMachine.status === "critical" ? "critical" : "warning",
      message: timeMachine.message,
      detail: `Share: ${timeMachine.path}. Last activity: ${timeMachine.latestActivityAt ?? "none"}. Warning threshold: ${timeMachine.staleAfterHours} hours. SMB service: ${timeMachine.serviceActive === null ? "unknown" : timeMachine.serviceActive ? "running" : "stopped"}.`,
    });
  }

  if (archiveBackup.configured && archiveBackup.state === "error") {
    issues.push({
      id: "archive-backup",
      level: "warning",
      message: archiveBackup.message,
      detail: archiveBackup.errorDetail ?? "See the archive service journal on the Pi for transfer diagnostics.",
      fingerprint: archiveBackup.errorDetail ?? archiveBackup.message,
    });
  }

  if (portableExport.configured && portableExport.state === "error") {
    issues.push({
      id: "portable-export",
      level: "warning",
      message: portableExport.message,
      detail: portableExport.errorDetail ?? "The Mac exporter did not complete. Folders ending in .partial are unfinished and must not be treated as completed backups.",
      fingerprint: portableExport.errorDetail ?? portableExport.message,
    });
  }

  if ((system.cpuTempC ?? 0) >= 75) {
    issues.push({
      id: "temperature",
      level: "critical",
      message: "Pi temperature is high",
      detail: `Measured temperature: ${system.cpuTempC}°C. Critical threshold: 75°C.`,
    });
  } else if ((system.cpuTempC ?? 0) >= 65) {
    issues.push({
      id: "temperature",
      level: "warning",
      message: "Pi is running warm",
      detail: `Measured temperature: ${system.cpuTempC}°C. Warning threshold: 65°C.`,
    });
  }
  if (system.memoryPct >= 90) {
    issues.push({
      id: "memory",
      level: "warning",
      message: "Memory use is above 90%",
      detail: `Measured memory use: ${system.memoryPct}%.`,
    });
  }
  if (system.disk.usedPct >= 90) {
    issues.push({
      id: "disk",
      level: "critical",
      message: "SSD use is above 90%",
      detail: `Storage at ${system.disk.mount} is ${system.disk.usedPct}% full.`,
    });
  }

  for (const issue of issues) {
    issue.scoreImpact = issue.level === "info" ? 0
      : issue.id === "network" ? 50
      : issue.id === "latency" ? 12
      : issue.id === "temperature" ? (issue.level === "critical" ? 15 : 6)
      : issue.id === "disk" ? 15
      : ["archive-backup", "portable-export"].includes(issue.id) ? 8
      : issue.level === "critical" ? 20 : 10;
  }
  return { issues, summary: summarizeIssues(issues) };
}

export function summarizeIssues(issues: StatusIssue[]): StatusSummary {
  const score = 100 - issues.reduce((sum, issue) => sum + (issue.scoreImpact ?? 0), 0);
  const visibleIssues = issues.filter((issue) => issue.level !== "info");
  const level = visibleIssues.some((issue) => issue.level === "critical")
    ? "critical"
    : visibleIssues.length > 0
      ? "warning"
      : issues.length > 0
        ? "unconfigured"
        : "healthy";
  const issueCount = visibleIssues.length;

  return {
      score: Math.max(0, score),
      level,
      message:
        level === "healthy"
          ? "All systems nominal"
          : level === "unconfigured"
            ? "Ready for final setup"
            : issueCount === 1
              ? "1 item needs attention"
              : `${issueCount} items need attention`,
  };
}
