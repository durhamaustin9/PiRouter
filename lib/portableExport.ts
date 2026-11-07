import { randomUUID, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type {
  PortableExportPauseReason,
  PortableExportState,
  PortableExportStatus,
} from "@/lib/types";

const STATES = new Set<PortableExportState>([
  "unconfigured",
  "waiting",
  "requested",
  "running",
  "paused",
  "success",
  "error",
]);
const HEARTBEAT_TIMEOUT_MS = 2 * 60_000;
const SESSION_ENDED_DETAIL = "This export session has ended; the next session will continue";
const SESSION_CONTINUATION_MESSAGE = "Continuing automatically; saved work is retained";
const ARCHIVE_CONTINUATION_MESSAGE = "Waiting for archive; resumes automatically";
const ARCHIVE_HANDOFF_MESSAGES = new Set([
  "Waiting for Pi archive status; folder progress is saved",
  "Pi is archiving; folder progress is saved",
  "Waiting for the Pi archive check; folder progress is saved",
  "Waiting for the Pi archive window; folder progress is saved",
  "Waiting to close the idle backup image before archiving",
  ARCHIVE_CONTINUATION_MESSAGE,
]);

let mutationQueue: Promise<unknown> = Promise.resolve();

function statusPath() {
  const configured = process.env.PORTABLE_EXPORT_STATUS_PATH?.trim();
  return configured
    ? path.resolve(configured)
    : path.join(process.cwd(), "data", "portable-export.json");
}

function isConfigured() {
  return Boolean(process.env.PORTABLE_EXPORT_TOKEN?.trim());
}

function automaticEnabled() {
  return process.env.PORTABLE_EXPORT_AUTOMATIC?.trim() !== "false";
}

function emptyStatus(): PortableExportStatus {
  const configured = isConfigured();
  return {
    configured,
    automatic: configured && automaticEnabled(),
    state: configured ? "waiting" : "unconfigured",
    message: configured
      ? "Waiting for the macOS exporter"
      : "Install and pair the macOS portable-export helper",
    requestId: null,
    requestedAt: null,
    lastAttemptAt: null,
    lastHeartbeatAt: null,
    lastSuccessAt: null,
    lastSnapshotName: null,
    activeSnapshotName: null,
    bytesCopied: null,
    filesCopied: null,
    errorDetail: null,
    pauseReason: null,
  };
}

function nullableString(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nullableNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function normalizePause(status: PortableExportStatus): PortableExportStatus {
  if (status.state !== "paused") return { ...status, pauseReason: null };
  const detail = status.errorDetail?.trim();
  // Only the exact legacy session-limit detail is routine. A lock, connection,
  // or data error must remain visible even if a stale reason says otherwise.
  if (detail && detail !== SESSION_ENDED_DETAIL) {
    return { ...status, pauseReason: null };
  }
  let reason: PortableExportPauseReason | null = null;
  if (status.pauseReason === "session-budget" || status.pauseReason === "archive-handoff") {
    reason = status.pauseReason;
  } else if (detail === SESSION_ENDED_DETAIL ||
    status.message === SESSION_CONTINUATION_MESSAGE ||
    status.message === SESSION_ENDED_DETAIL ||
    status.message === `Export paused; saved work is retained. ${SESSION_ENDED_DETAIL}`) {
    reason = "session-budget";
  } else if (ARCHIVE_HANDOFF_MESSAGES.has(status.message)) {
    reason = "archive-handoff";
  }
  return reason ? {
    ...status,
    pauseReason: reason,
    message: reason === "session-budget"
      ? SESSION_CONTINUATION_MESSAGE
      : ARCHIVE_CONTINUATION_MESSAGE,
    errorDetail: null,
  } : { ...status, pauseReason: null };
}

function normalizeStatus(value: unknown): PortableExportStatus {
  const fallback = emptyStatus();
  if (!value || typeof value !== "object") return fallback;
  const parsed = value as Partial<PortableExportStatus>;
  const state =
    typeof parsed.state === "string" &&
    STATES.has(parsed.state as PortableExportState)
      ? (parsed.state as PortableExportState)
      : fallback.state;

  const status: PortableExportStatus = {
    configured: fallback.configured,
    automatic: fallback.automatic,
    state: fallback.configured ? state : "unconfigured",
    message:
      typeof parsed.message === "string" && parsed.message.length > 0
        ? parsed.message
        : fallback.message,
    requestId: nullableString(parsed.requestId),
    requestedAt: nullableString(parsed.requestedAt),
    lastAttemptAt: nullableString(parsed.lastAttemptAt),
    lastHeartbeatAt: nullableString(parsed.lastHeartbeatAt),
    lastSuccessAt: nullableString(parsed.lastSuccessAt),
    lastSnapshotName: nullableString(parsed.lastSnapshotName),
    activeSnapshotName: nullableString(parsed.activeSnapshotName),
    bytesCopied: nullableNumber(parsed.bytesCopied),
    filesCopied: nullableNumber(parsed.filesCopied),
    errorDetail: nullableString(parsed.errorDetail),
    pauseReason: parsed.pauseReason,
    verified: parsed.verified === true,
  };
  const heartbeat = Date.parse(status.lastHeartbeatAt ?? status.lastAttemptAt ?? "");
  if (
    status.state === "running" &&
    (!Number.isFinite(heartbeat) || Date.now() - heartbeat > HEARTBEAT_TIMEOUT_MS)
  ) {
    return {
      ...status,
      state: "paused",
      message: "Waiting for the Mac exporter; uploaded work is retained",
      pauseReason: null,
    };
  }
  return normalizePause(status);
}

export function portableExportAuthorized(authorization: string | null) {
  const token = process.env.PORTABLE_EXPORT_TOKEN?.trim();
  if (!token || !authorization?.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(authorization.slice(7));
  const expected = Buffer.from(token);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export async function readPortableExportStatus() {
  try {
    const contents = await fs.readFile(statusPath(), "utf8");
    return normalizeStatus(JSON.parse(contents));
  } catch {
    return emptyStatus();
  }
}

async function writePortableExportStatus(status: PortableExportStatus) {
  const target = statusPath();
  await fs.mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(status, null, 2)}\n`, {
    mode: 0o600,
  });
  await fs.rename(temporary, target);
  return status;
}

function mutateStatus(
  mutation: (current: PortableExportStatus) => PortableExportStatus,
) {
  const operation = mutationQueue.then(async () => {
    const current = await readPortableExportStatus();
    return writePortableExportStatus(mutation(current));
  });
  mutationQueue = operation.catch(() => undefined);
  return operation;
}

export async function requestPortableExport(trigger: "automatic" | "manual") {
  return mutateStatus((current) => {
    if (!current.configured) return current;
    if (current.state === "requested" || current.state === "running") {
      return current;
    }
    const now = new Date().toISOString();
    const resuming =
      (current.state === "paused" || current.state === "error") &&
      Boolean(current.requestId);
    return {
      ...current,
      state: "requested",
      message:
        resuming
          ? "Export resumed · waiting for the Mac"
          : trigger === "automatic"
          ? "A new Time Machine snapshot is ready to export"
          : "Export requested · waiting for the Mac",
      requestId: resuming ? current.requestId : randomUUID(),
      requestedAt: resuming ? current.requestedAt ?? now : now,
      lastAttemptAt: now,
      lastHeartbeatAt: resuming ? current.lastHeartbeatAt : null,
      activeSnapshotName: resuming ? current.activeSnapshotName : null,
      bytesCopied: resuming ? current.bytesCopied : null,
      filesCopied: resuming ? current.filesCopied : null,
      errorDetail: null,
      pauseReason: null,
      verified: false,
    };
  });
}

export type PortableExportUpdate = {
  requestId: string;
  state: "running" | "paused" | "success" | "error";
  message: string;
  lastSnapshotName?: string | null;
  activeSnapshotName?: string | null;
  bytesCopied?: number | null;
  filesCopied?: number | null;
  errorDetail?: string | null;
  pauseReason?: PortableExportPauseReason | null;
  verified?: boolean;
};

export async function updatePortableExport(update: PortableExportUpdate) {
  return mutateStatus((current) => {
    if (!current.requestId || update.requestId !== current.requestId) {
      throw new Error("The portable-export request is no longer active");
    }
    if (update.state === "success" && update.verified !== true) {
      throw new Error("A portable export must be verified before it can be marked successful");
    }
    for (const value of [update.bytesCopied, update.filesCopied]) {
      if (value != null && nullableNumber(value) === null) {
        throw new Error("Portable-export progress must be a finite nonnegative number");
      }
    }
    const completed = update.state === "success";
    const now = new Date().toISOString();
    return normalizePause({
      ...current,
      state: update.state,
      message: update.message,
      errorDetail:
        update.state === "error" || update.state === "paused"
          ? nullableString(update.errorDetail)
          : null,
      pauseReason: update.pauseReason,
      verified: completed,
      requestId: completed ? null : current.requestId,
      requestedAt: completed ? null : current.requestedAt,
      lastAttemptAt:
        update.state === "running" && current.state !== "running"
          ? now
          : current.lastAttemptAt,
      lastHeartbeatAt:
        update.state === "running" ? now : current.lastHeartbeatAt,
      lastSuccessAt:
        completed ? now : current.lastSuccessAt,
      lastSnapshotName:
        completed
          ? nullableString(update.lastSnapshotName) ??
            nullableString(update.activeSnapshotName) ??
            current.activeSnapshotName ??
            current.lastSnapshotName
          : current.lastSnapshotName,
      activeSnapshotName:
        completed
          ? null
          : nullableString(update.activeSnapshotName) ?? current.activeSnapshotName,
      bytesCopied: nullableNumber(update.bytesCopied) ?? current.bytesCopied,
      filesCopied: nullableNumber(update.filesCopied) ?? current.filesCopied,
    });
  });
}
