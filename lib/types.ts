export type StatusLevel =
  | "healthy"
  | "warning"
  | "critical"
  | "unconfigured"
  | "unavailable";

export type IssueLevel = "warning" | "critical" | "info";

export interface StatusIssue {
  id: string;
  level: IssueLevel;
  message: string;
  detail?: string;
  scoreImpact?: number;
  fingerprint?: string;
  firstSeenAt?: string;
  dismissedUntil?: string;
}

export interface StatusSummary {
  score: number;
  level: StatusLevel;
  message: string;
}

export interface NetworkStatus {
  interface: string | null;
  online: boolean;
  ipv4: string | null;
  downloadBps: number;
  uploadBps: number;
  linkMbps: number | null;
  latencyMs: number | null;
  packetLossPct: number | null;
  dnsOk: boolean;
  healthTarget: string;
}

export interface TailscaleStatus {
  installed: boolean;
  online: boolean;
  backendState: string;
  ip: string | null;
  peerCount: number;
  onlinePeerCount: number;
  directPeerCount: number;
  hostname: string | null;
}

export interface EndpointStatus {
  configured: boolean;
  label: string;
  host: string | null;
  port: number;
  reachable: boolean | null;
  latencyMs: number | null;
  message: string;
}

export interface TimeMachineStatus {
  configured: boolean;
  path: string | null;
  exists: boolean;
  serviceActive: boolean | null;
  /** Server-side share activity; does not confirm a running macOS backup. */
  backupInProgress: boolean | null;
  latestActivityAt: string | null;
  ageHours: number | null;
  staleAfterHours: number;
  status: StatusLevel;
  message: string;
  reason?: "unconfigured" | "missing" | "unreadable" | "service" | "empty" | "stale" | "healthy";
}

export type PortableExportState =
  | "unconfigured"
  | "waiting"
  | "requested"
  | "running"
  | "paused"
  | "success"
  | "error";

export type PortableExportPauseReason = "session-budget" | "archive-handoff";

export interface PortableExportStatus {
  configured: boolean;
  automatic: boolean;
  state: PortableExportState;
  message: string;
  requestId: string | null;
  requestedAt: string | null;
  lastAttemptAt: string | null;
  lastHeartbeatAt?: string | null;
  lastSuccessAt: string | null;
  lastSnapshotName: string | null;
  activeSnapshotName?: string | null;
  bytesCopied: number | null;
  filesCopied?: number | null;
  errorDetail?: string | null;
  pauseReason?: PortableExportPauseReason | null;
  verified?: boolean;
}

export type ArchiveBackupState =
  | "unconfigured"
  | "waiting"
  | "running"
  | "maintenance"
  | "success"
  | "error";

export interface ArchiveBackupStatus {
  configured: boolean;
  automatic: boolean;
  state: ArchiveBackupState;
  message: string;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  lastSnapshotId: string | null;
  dataAddedBytes: number | null;
  progressPercent: number | null;
  bytesProcessed: number | null;
  totalBytes: number | null;
  filesProcessed: number | null;
  totalFiles: number | null;
  currentFile: string | null;
  elapsedSeconds: number | null;
  estimatedSecondsRemaining: number | null;
  progressUpdatedAt: string | null;
  errorDetail?: string | null;
  lastCleanupAt?: string | null;
}

export interface ArchiveCoordination {
  configured: boolean;
  state: ArchiveBackupState;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  lastSnapshotId: string | null;
  serviceActive: boolean | null;
  needsArchive: boolean | null;
}

export interface DiskStatus {
  path: string;
  filesystem: string;
  mount: string;
  usedBytes: number;
  totalBytes: number;
  usedPct: number;
}

export interface SystemStatus {
  hostname: string;
  uptimeSeconds: number;
  cpuLoadPct: number;
  cpuTempC: number | null;
  memoryUsedBytes: number;
  memoryTotalBytes: number;
  memoryPct: number;
  disk: DiskStatus;
}

export interface PiStatus {
  capturedAt: string;
  identity: {
    name: string;
    location: string;
  };
  summary: StatusSummary;
  network: NetworkStatus;
  tailscale: TailscaleStatus;
  endpoint: EndpointStatus;
  timeMachine: TimeMachineStatus;
  archiveBackup: ArchiveBackupStatus;
  portableExport: PortableExportStatus;
  system: SystemStatus;
  issues: StatusIssue[];
  dismissedIssues: StatusIssue[];
}

export interface HistoryPoint {
  capturedAt: string;
  downloadBps: number;
  uploadBps: number;
  latencyMs: number | null;
  cpuTempC: number | null;
  cpuLoadPct: number;
  memoryPct: number;
  diskPct: number;
}

export interface SpeedTestResult {
  downloadMbps: number;
  uploadMbps: number;
  latencyMs: number;
  packetLossPct: number | null;
  serverName: string | null;
  isp: string | null;
  capturedAt: string;
}

export type SpeedTestJobState = "idle" | "running" | "complete" | "error";

export type SpeedTestPhase =
  | "idle"
  | "selecting"
  | "latency"
  | "download"
  | "upload"
  | "complete"
  | "error";

export interface SpeedTestJobStatus {
  jobId: string | null;
  state: SpeedTestJobState;
  phase: SpeedTestPhase;
  progressPct: number;
  message: string;
  startedAt: string | null;
  completedAt: string | null;
  elapsedSeconds: number;
  live: {
    latencyMs: number | null;
    downloadMbps: number | null;
    uploadMbps: number | null;
  };
  result: SpeedTestResult | null;
  error: string | null;
}

export interface SpeedTestStartResponse {
  started: boolean;
  job: SpeedTestJobStatus;
}
