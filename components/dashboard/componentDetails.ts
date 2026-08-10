import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import {
  faArrowTrendUp,
  faBell,
  faClockRotateLeft,
  faCloudArrowUp,
  faDatabase,
  faGlobe,
  faHardDrive,
  faHeartPulse,
  faMicrochip,
  faRoute,
  faShieldHalved,
  faWifi,
} from "@fortawesome/free-solid-svg-icons";
import type {
  HistoryPoint,
  PiStatus,
  SpeedTestJobStatus,
  StatusLevel,
} from "@/lib/types";
import {
  archiveBackupLevel,
  formatAge,
  formatBytes,
  formatDateTime,
  formatDuration,
  formatPercent,
  formatRate,
  formatUptime,
  portableExportLevel,
  systemLevel,
  timeMachineDisplay,
} from "@/components/dashboard/formatters";

export type ComponentDetailId =
  | "overall-health"
  | "network-traffic"
  | "time-machine"
  | "endpoint-route"
  | "tailscale"
  | "pi-system"
  | "link-health"
  | "internet-quality"
  | "archive-backup"
  | "portable-export"
  | "attention"
  | "ssd-storage"
  | "health-history";

export interface ComponentDetail {
  title: string;
  kicker: string;
  description: string;
  icon: IconDefinition;
  level: StatusLevel;
  metrics: Array<{ label: string; value: string; hint: string }>;
  progress?: {
    percent: number;
    value: string;
    label: string;
    detail: string;
  };
  note: string;
  updatedAt: string;
}

type DetailContext = {
  status: PiStatus;
  history: HistoryPoint[];
  speedTestJob: SpeedTestJobStatus;
};

const displayLevel = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1);

export function buildComponentDetail(
  id: ComponentDetailId,
  { status, history, speedTestJob }: DetailContext,
): ComponentDetail {
  const updatedAt = status.capturedAt;
  const deviceName = status.identity.name;
  const timeMachine = timeMachineDisplay(status.timeMachine);
  const networkLevel: StatusLevel = status.network.online
    ? "healthy"
    : "critical";

  switch (id) {
    case "overall-health":
      return {
        title: "Overall health",
        kicker: "Combined status",
        description:
          "A weighted summary of the Pi, network, storage, and essential services.",
        icon: faHeartPulse,
        level: status.summary.level,
        metrics: [
          {
            label: "Health score",
            value: `${status.summary.score} / 100`,
            hint: "Deductions come from active warnings and critical checks.",
          },
          {
            label: "Current state",
            value: status.summary.message,
            hint: "The highest-priority condition across every monitored component.",
          },
          {
            label: "Attention items",
            value: `${status.issues.length}`,
            hint: "Warnings and failures that may need action.",
          },
          {
            label: "Last checked",
            value: new Date(updatedAt).toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
            }),
            hint: "The dashboard refreshes core status every ten seconds.",
          },
        ],
        note: "This score describes current health; it is not an uptime percentage.",
        updatedAt,
      };
    case "network-traffic":
      return {
        title: "Network throughput",
        kicker: "Live interface traffic",
        description:
          `The amount of data currently crossing ${deviceName}'s active network interface.`,
        icon: faArrowTrendUp,
        level: networkLevel,
        metrics: [
          {
            label: "Download now",
            value: formatRate(status.network.downloadBps),
            hint: "Traffic received by the Pi during the latest sample.",
          },
          {
            label: "Upload now",
            value: formatRate(status.network.uploadBps),
            hint: "Traffic sent by the Pi during the latest sample.",
          },
          {
            label: "Interface",
            value: status.network.interface ?? "Unknown",
            hint: "The Linux network device being measured.",
          },
          {
            label: "Link capacity",
            value: status.network.linkMbps
              ? `${status.network.linkMbps} Mbps`
              : "Unknown",
            hint: "Negotiated Ethernet capacity, not an internet speed result.",
          },
        ],
        note: "Short bursts are normal. Use Internet quality for a controlled speed test.",
        updatedAt,
      };
    case "time-machine":
      return {
        title: "Time Machine",
        kicker: "Backup share activity",
        description:
          "Shows whether macOS is actively writing to the SMB backup bundle and when files last changed.",
        icon: faClockRotateLeft,
        level: status.timeMachine.configured
          ? status.timeMachine.status
          : "unconfigured",
        metrics: [
          {
            label: "Current state",
            value: timeMachine.headline,
            hint: timeMachine.detail,
          },
          {
            label: "Last file activity",
            value: formatAge(status.timeMachine.latestActivityAt),
            hint: "File activity is not presented as a completed-backup timestamp.",
          },
          {
            label: "SMB service",
            value:
              status.timeMachine.serviceActive === null
                ? "Unknown"
                : status.timeMachine.serviceActive
                  ? "Running"
                  : "Stopped",
            hint: "Samba publishes the Time Machine share to your Macs.",
          },
          {
            label: "Backup storage",
            value: `${status.system.disk.usedPct}% used`,
            hint: `${formatBytes(status.system.disk.totalBytes - status.system.disk.usedBytes)} remains free.`,
          },
        ],
        note: "Portable exports use completed, immutable snapshots and never modify this live backup.",
        updatedAt,
      };
    case "endpoint-route":
      return {
        title: status.endpoint.label,
        kicker: "TCP reachability",
        description:
          "A lightweight connection check to a configurable service on your LAN, tailnet, or the internet.",
        icon: faRoute,
        level: !status.endpoint.configured
          ? "unconfigured"
          : status.endpoint.reachable
            ? "healthy"
            : "critical",
        metrics: [
          {
            label: "Route status",
            value: status.endpoint.reachable ? "Open" : "Unavailable",
            hint: status.endpoint.message,
          },
          {
            label: "Target",
            value: status.endpoint.host
              ? `${status.endpoint.host}:${status.endpoint.port}`
              : "Not configured",
            hint: "The hostname and TCP port configured for this check.",
          },
          {
            label: "Connection time",
            value:
              status.endpoint.latencyMs === null ? "—" : `${status.endpoint.latencyMs} ms`,
            hint: "Time required to establish the TCP connection.",
          },
          {
            label: "Transport",
            value: "TCP",
            hint: "Only a connection handshake is attempted; no application data is sent.",
          },
        ],
        note: "A successful connection confirms reachability, not the health of the remote application.",
        updatedAt,
      };
    case "tailscale":
      return {
        title: "Tailscale mesh",
        kicker: "Private network",
        description:
          `${deviceName}'s encrypted tailnet connection and the peers it can currently see.`,
        icon: faShieldHalved,
        level: !status.tailscale.installed
          ? "unavailable"
          : status.tailscale.online
            ? "healthy"
            : "critical",
        metrics: [
          {
            label: "Backend",
            value: status.tailscale.backendState,
            hint: "The state reported by the local Tailscale daemon.",
          },
          {
            label: "Tailnet IP",
            value: status.tailscale.ip ?? "Unavailable",
            hint: `${deviceName}'s stable private address.`,
          },
          {
            label: "Peers online",
            value: `${status.tailscale.onlinePeerCount} of ${status.tailscale.peerCount}`,
            hint: "Known peers currently advertising as online.",
          },
          {
            label: "Direct peers",
            value: `${status.tailscale.directPeerCount}`,
            hint: "Peers connected directly instead of through a relay.",
          },
        ],
        note: "Private services can use this mesh without being exposed to the public internet.",
        updatedAt,
      };
    case "pi-system":
      return {
        title: "Raspberry Pi system",
        kicker: "Compute health",
        description:
          "Current processor, memory, temperature, and uptime readings from the Pi 5.",
        icon: faMicrochip,
        level: systemLevel(status.system),
        metrics: [
          {
            label: "Uptime",
            value: formatUptime(status.system.uptimeSeconds),
            hint: "Time since the Pi last booted.",
          },
          {
            label: "CPU load",
            value: `${status.system.cpuLoadPct}%`,
            hint: "One-minute processor load normalized across CPU cores.",
          },
          {
            label: "Temperature",
            value:
              status.system.cpuTempC === null
                ? "Unknown"
                : `${status.system.cpuTempC}°C`,
            hint: "Thermal throttling begins near 85°C.",
          },
          {
            label: "Memory",
            value: `${status.system.memoryPct}% used`,
            hint: `${formatBytes(status.system.memoryUsedBytes)} of ${formatBytes(status.system.memoryTotalBytes)} is in use.`,
          },
        ],
        note: "The display sleeping does not suspend the Pi or these background services.",
        updatedAt,
      };
    case "link-health":
      return {
        title: "Ethernet link health",
        kicker: "Local network",
        description:
          `Connection details for the physical interface carrying ${deviceName}'s traffic.`,
        icon: faWifi,
        level: networkLevel,
        metrics: [
          {
            label: "Interface",
            value: status.network.interface ?? "Unknown",
            hint: "The active Linux device selected by PiRouter.",
          },
          {
            label: "IPv4 address",
            value: status.network.ipv4 ?? "Unavailable",
            hint: `${deviceName}'s address on your local network.`,
          },
          {
            label: "Negotiated speed",
            value: status.network.linkMbps
              ? `${status.network.linkMbps} Mbps`
              : "Unknown",
            hint: "The wired link rate negotiated with the switch or router.",
          },
          {
            label: "DNS lookup",
            value: status.network.dnsOk ? "Healthy" : "Failed",
            hint: "Confirms the Pi can resolve hostnames.",
          },
        ],
        note: "A healthy 1 Gbps link leaves ample capacity for Time Machine traffic.",
        updatedAt,
      };
    case "internet-quality": {
      const result = speedTestJob.result;
      return {
        title: "Internet quality",
        kicker: "External connectivity",
        description:
          "Lightweight reachability checks plus the latest optional Ookla speed test.",
        icon: faGlobe,
        level: networkLevel,
        metrics: [
          {
            label: "Latency",
            value:
              status.network.latencyMs === null
                ? "Unknown"
                : `${status.network.latencyMs} ms`,
            hint: `Round-trip time to ${status.network.healthTarget}.`,
          },
          {
            label: "Packet loss",
            value:
              status.network.packetLossPct === null
                ? "Unknown"
                : `${status.network.packetLossPct}%`,
            hint: "Packets lost during the lightweight health probe.",
          },
          {
            label: "Latest download",
            value: result ? `${result.downloadMbps} Mbps` : "Not tested",
            hint: "The most recent on-demand speed-test result.",
          },
          {
            label: "Latest upload",
            value: result ? `${result.uploadMbps} Mbps` : "Not tested",
            hint: result
              ? `Captured ${formatDateTime(result.capturedAt)}.`
              : "Run a speed test from the Network screen.",
          },
        ],
        note: "Health probes are lightweight; the bandwidth test runs only when requested.",
        updatedAt,
      };
    }
    case "archive-backup": {
      const archive = status.archiveBackup;
      const hasLiveProgress =
        archive.state === "running" && archive.progressPercent !== null;
      const processedDetail =
        archive.bytesProcessed !== null && archive.totalBytes !== null
          ? `${formatBytes(archive.bytesProcessed)} of ${formatBytes(archive.totalBytes)} source data processed`
          : "Restic is scanning the Time Machine bundle";
      const liveMetrics = hasLiveProgress
        ? [
            {
              label: "Current state",
              value: "Actively transferring",
              hint: archive.message,
            },
            {
              label: "Source processed",
              value:
                archive.bytesProcessed !== null && archive.totalBytes !== null
                  ? `${formatBytes(archive.bytesProcessed)} / ${formatBytes(archive.totalBytes)}`
                  : "Calculating",
              hint: "Deduplication means actual network and repository bytes can be smaller.",
            },
            {
              label: "Latest activity",
              value: formatAge(archive.progressUpdatedAt),
              hint: "A fresh timestamp confirms Restic is still reporting progress.",
            },
            {
              label: "Current item",
              value: archive.currentFile ?? "Scanning bundle",
              hint: "The Time Machine sparsebundle item currently being processed.",
            },
            {
              label: "Files processed",
              value:
                archive.filesProcessed !== null && archive.totalFiles !== null
                  ? `${archive.filesProcessed.toLocaleString()} / ${archive.totalFiles.toLocaleString()}`
                  : "Calculating",
              hint: "Large sparsebundle bands make byte progress more useful than file count.",
            },
            {
              label: "Elapsed",
              value: formatDuration(archive.elapsedSeconds),
              hint: "Time spent in the current Restic attempt.",
            },
          ]
        : [
            {
              label: "Current state",
              value: displayLevel(archive.state),
              hint: archive.message,
            },
            {
              label: "Last attempt",
              value: formatDateTime(archive.lastAttemptAt),
              hint: "The most recent automatic or manual archive check.",
            },
            {
              label: "Last snapshot",
              value: formatDateTime(archive.lastSuccessAt),
              hint: archive.lastSnapshotId
                ? `Restic snapshot ${archive.lastSnapshotId.slice(0, 8)}.`
                : "No complete Unraid snapshot yet.",
            },
            {
              label: "Automation",
              value: archive.automatic ? "Every 30 min" : "Disabled",
              hint: "A transfer begins only after Time Machine has been quiet for 20 minutes.",
            },
          ];
      return {
        title: "Unraid archive",
        kicker: "Independent secondary backup",
        description:
          "Encrypted, deduplicated Restic snapshots copied directly from the Pi without relying on the Mac.",
        icon: faCloudArrowUp,
        level: archiveBackupLevel(archive),
        metrics: liveMetrics,
        progress: hasLiveProgress
          ? {
              percent: archive.progressPercent ?? 0,
              value: formatPercent(archive.progressPercent ?? 0),
              label: "Live Restic progress",
              detail: `${processedDetail} · updated ${formatAge(archive.progressUpdatedAt)}`,
            }
          : undefined,
        note: hasLiveProgress
          ? "Processed bytes prove the job is advancing. Restic deduplicates before upload, so they are not the exact number of network bytes sent."
          : "Interrupted uploads retry from deduplicated chunks. Restoring this archive requires Restic.",
        updatedAt,
      };
    }
    case "portable-export":
      return {
        title: "Browsable exports",
        kicker: "Portable file recovery",
        description:
          "Completed Time Machine snapshots exported by macOS as ordinary folders on a secondary server.",
        icon: faCloudArrowUp,
        level: portableExportLevel(status.portableExport),
        metrics: [
          {
            label: "Current state",
            value: displayLevel(status.portableExport.state),
            hint: status.portableExport.message,
          },
          {
            label: "Requested",
            value: formatDateTime(status.portableExport.requestedAt),
            hint: "The Mac claims manual requests from the dashboard.",
          },
          {
            label: "Last export",
            value: formatDateTime(status.portableExport.lastSuccessAt),
            hint: status.portableExport.lastSnapshotName
              ? `Snapshot ${status.portableExport.lastSnapshotName}.`
              : "No completed portable export yet.",
          },
          {
            label: "Automation",
            value: status.portableExport.automatic ? "Daily" : "Manual",
            hint: "Daily exports use the newest completed Time Machine snapshot.",
          },
        ],
        note: "Timestamped folders are directly browsable; unchanged files are hard-linked to save space.",
        updatedAt,
      };
    case "attention": {
      const metrics = status.issues.length
        ? status.issues.slice(0, 4).map((issue) => ({
            label: displayLevel(issue.level),
            value: issue.message,
            hint: "This condition is included in the overall health score.",
          }))
        : [
            {
              label: "Active notices",
              value: "None",
              hint: "Every monitored component is within its healthy range.",
            },
          ];
      return {
        title: "Attention items",
        kicker: "Actionable notices",
        description:
          "Warnings, failures, and setup items that affect the overall health score.",
        icon: faBell,
        level: status.issues.some((issue) => issue.level === "critical")
          ? "critical"
          : status.issues.length
            ? "warning"
            : "healthy",
        metrics,
        note: "The list clears automatically when the underlying check recovers.",
        updatedAt,
      };
    }
    case "ssd-storage":
      return {
        title: "SSD storage",
        kicker: "Time Machine volume",
        description:
          `Capacity and filesystem information for the external SSD mounted on ${deviceName}.`,
        icon: faHardDrive,
        level: status.system.disk.usedPct > 90 ? "critical" : "healthy",
        metrics: [
          {
            label: "Free space",
            value: formatBytes(
              status.system.disk.totalBytes - status.system.disk.usedBytes,
            ),
            hint: "Space currently available to Time Machine.",
          },
          {
            label: "Used space",
            value: `${formatBytes(status.system.disk.usedBytes)} · ${status.system.disk.usedPct}%`,
            hint: "All data currently stored on the backup filesystem.",
          },
          {
            label: "Total capacity",
            value: formatBytes(status.system.disk.totalBytes),
            hint: "Usable formatted capacity reported by Linux.",
          },
          {
            label: "Filesystem",
            value: status.system.disk.filesystem,
            hint: `Mounted at ${status.system.disk.mount}.`,
          },
        ],
        note: "The dashboard raises a storage warning before usage reaches a critical level.",
        updatedAt,
      };
    case "health-history": {
      const oldest = history[0]?.capturedAt ?? null;
      const newest = history.at(-1)?.capturedAt ?? null;
      return {
        title: "Health history",
        kicker: "Local trend samples",
        description:
          "Recent network and system samples used to show short-term activity trends.",
        icon: faDatabase,
        level: "healthy",
        metrics: [
          {
            label: "Samples loaded",
            value: `${history.length}`,
            hint: "The current screen loads up to 90 recent samples.",
          },
          {
            label: "Oldest visible",
            value: oldest ? formatDateTime(oldest) : "Not available",
            hint: "Beginning of the currently loaded chart window.",
          },
          {
            label: "Newest visible",
            value: newest ? formatDateTime(newest) : "Not available",
            hint: "Most recent stored trend sample.",
          },
          {
            label: "Retention",
            value: "7 days",
            hint: "Older health samples are removed automatically.",
          },
        ],
        note: "History stays in a local SQLite database and is not uploaded.",
        updatedAt,
      };
    }
  }
}
