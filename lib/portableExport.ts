import { randomUUID, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type {
  PortableExportState,
  PortableExportStatus,
} from "@/lib/types";

const STATES = new Set<PortableExportState>([
  "unconfigured",
  "waiting",
  "requested",
  "running",
  "success",
  "error",
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
    lastSuccessAt: null,
    lastSnapshotName: null,
    bytesCopied: null,
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

function normalizeStatus(value: unknown): PortableExportStatus {
  const fallback = emptyStatus();
  if (!value || typeof value !== "object") return fallback;
  const parsed = value as Partial<PortableExportStatus>;
  const state =
    typeof parsed.state === "string" &&
    STATES.has(parsed.state as PortableExportState)
      ? (parsed.state as PortableExportState)
      : fallback.state;

  return {
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
    lastSuccessAt: nullableString(parsed.lastSuccessAt),
    lastSnapshotName: nullableString(parsed.lastSnapshotName),
    bytesCopied: nullableNumber(parsed.bytesCopied),
  };
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
    return {
      ...current,
      state: "requested",
      message:
        trigger === "automatic"
          ? "A new Time Machine snapshot is ready to export"
          : "Export requested · waiting for the Mac",
      requestId: randomUUID(),
      requestedAt: now,
      lastAttemptAt: now,
      bytesCopied: null,
    };
  });
}

export type PortableExportUpdate = {
  requestId: string;
  state: "running" | "success" | "error";
  message: string;
  lastSnapshotName?: string | null;
  bytesCopied?: number | null;
};

export async function updatePortableExport(update: PortableExportUpdate) {
  return mutateStatus((current) => {
    if (!current.requestId || update.requestId !== current.requestId) {
      throw new Error("The portable-export request is no longer active");
    }
    const completed = update.state === "success" || update.state === "error";
    return {
      ...current,
      state: update.state,
      message: update.message,
      requestId: completed ? null : current.requestId,
      requestedAt: completed ? null : current.requestedAt,
      lastSuccessAt:
        update.state === "success"
          ? new Date().toISOString()
          : current.lastSuccessAt,
      lastSnapshotName:
        update.state === "success"
          ? nullableString(update.lastSnapshotName) ?? current.lastSnapshotName
          : current.lastSnapshotName,
      bytesCopied:
        update.state === "success"
          ? nullableNumber(update.bytesCopied)
          : current.bytesCopied,
    };
  });
}
