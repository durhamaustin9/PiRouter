import type {
  ArchiveBackupStatus,
  EndpointStatus,
  NetworkStatus,
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
  archive: ArchiveBackupStatus;
  system: SystemStatus;
};

export function summarizeStatus({
  network,
  tailscale,
  endpoint,
  timeMachine,
  archive,
  system,
}: SummaryInput): { issues: StatusIssue[]; summary: StatusSummary } {
  const issues: StatusIssue[] = [];
  let score = 100;

  if (!network.online) {
    score -= 50;
    issues.push({
      id: "network",
      level: "critical",
      message: "Internet health check failed",
    });
  } else if (
    (network.packetLossPct ?? 0) > 2 ||
    (network.latencyMs ?? 0) > 100
  ) {
    score -= 12;
    issues.push({
      id: "latency",
      level: "warning",
      message: "Network quality is degraded",
    });
  }

  if (!tailscale.installed) {
    score -= 8;
    issues.push({
      id: "tailscale",
      level: "info",
      message: "Tailscale is not installed",
    });
  } else if (!tailscale.online) {
    score -= 20;
    issues.push({
      id: "tailscale",
      level: "critical",
      message: "Tailscale is offline",
    });
  }

  if (!endpoint.configured) {
    issues.push({
      id: "endpoint",
      level: "info",
      message: "Service endpoint is not configured",
    });
  } else if (!endpoint.reachable) {
    score -= 20;
    issues.push({
      id: "endpoint",
      level: "critical",
      message: `${endpoint.label} is unreachable`,
    });
  }

  if (!timeMachine.configured) {
    issues.push({
      id: "time-machine",
      level: "info",
      message: "Time Machine path is not configured",
    });
  } else if (timeMachine.status !== "healthy") {
    score -= timeMachine.status === "critical" ? 20 : 10;
    issues.push({
      id: "time-machine",
      level: timeMachine.status === "critical" ? "critical" : "warning",
      message: timeMachine.message,
    });
  }

  if (archive.configured && archive.state === "error") {
    score -= 8;
    issues.push({
      id: "archive",
      level: "warning",
      message: archive.message,
    });
  }

  if ((system.cpuTempC ?? 0) >= 75) {
    score -= 15;
    issues.push({
      id: "temperature",
      level: "critical",
      message: "Pi temperature is high",
    });
  } else if ((system.cpuTempC ?? 0) >= 65) {
    score -= 6;
    issues.push({
      id: "temperature",
      level: "warning",
      message: "Pi is running warm",
    });
  }
  if (system.memoryPct >= 90) {
    score -= 10;
    issues.push({
      id: "memory",
      level: "warning",
      message: "Memory use is above 90%",
    });
  }
  if (system.disk.usedPct >= 90) {
    score -= 15;
    issues.push({
      id: "disk",
      level: "critical",
      message: "SSD use is above 90%",
    });
  }

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
    issues,
    summary: {
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
    },
  };
}
