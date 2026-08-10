import { promises as fs } from "node:fs";
import type {
  ArchiveBackupState,
  ArchiveBackupStatus,
} from "@/lib/types";

const STATES = new Set<ArchiveBackupState>([
  "unconfigured",
  "waiting",
  "running",
  "maintenance",
  "success",
  "error",
]);

function emptyStatus(): ArchiveBackupStatus {
  return {
    configured: false,
    automatic: false,
    state: "unconfigured",
    message: "Install independent Unraid archiving on the Pi",
    lastAttemptAt: null,
    lastSuccessAt: null,
    lastSnapshotId: null,
    dataAddedBytes: null,
    progressPercent: null,
    bytesProcessed: null,
    totalBytes: null,
    filesProcessed: null,
    totalFiles: null,
    currentFile: null,
    elapsedSeconds: null,
    estimatedSecondsRemaining: null,
    progressUpdatedAt: null,
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

export async function readArchiveBackupStatus(): Promise<ArchiveBackupStatus> {
  const statusPath =
    process.env.ARCHIVE_STATUS_PATH?.trim() ||
    "/var/lib/pirouter/archive-status.json";
  try {
    const parsed = JSON.parse(
      await fs.readFile(/* turbopackIgnore: true */ statusPath, "utf8"),
    ) as Partial<ArchiveBackupStatus>;
    const state =
      typeof parsed.state === "string" &&
      STATES.has(parsed.state as ArchiveBackupState)
        ? (parsed.state as ArchiveBackupState)
        : "waiting";
    return {
      configured: parsed.configured === true,
      automatic: parsed.automatic === true,
      state,
      message:
        nullableString(parsed.message) ?? "Waiting for the first Unraid archive",
      lastAttemptAt: nullableString(parsed.lastAttemptAt),
      lastSuccessAt: nullableString(parsed.lastSuccessAt),
      lastSnapshotId: nullableString(parsed.lastSnapshotId),
      dataAddedBytes: nullableNumber(parsed.dataAddedBytes),
      progressPercent: nullableNumber(parsed.progressPercent),
      bytesProcessed: nullableNumber(parsed.bytesProcessed),
      totalBytes: nullableNumber(parsed.totalBytes),
      filesProcessed: nullableNumber(parsed.filesProcessed),
      totalFiles: nullableNumber(parsed.totalFiles),
      currentFile: nullableString(parsed.currentFile),
      elapsedSeconds: nullableNumber(parsed.elapsedSeconds),
      estimatedSecondsRemaining: nullableNumber(
        parsed.estimatedSecondsRemaining,
      ),
      progressUpdatedAt: nullableString(parsed.progressUpdatedAt),
    };
  } catch {
    return emptyStatus();
  }
}
