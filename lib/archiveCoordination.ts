import { promises as fs } from "node:fs";
import path from "node:path";
import { readArchiveBackupStatus } from "@/lib/archiveBackup";
import { runCommand } from "@/lib/status/shared";
import type { ArchiveCoordination } from "@/lib/types";

type CommandRunner = typeof runCommand;
const sourceProbes = new Map<string, {
  expiresAt: number;
  result: Promise<boolean | null>;
}>();

export function archiveWorkerActive(state: string | null): boolean | null {
  if (state === null) return null;
  if (["active", "activating", "deactivating", "reloading"].includes(state.trim())) {
    return true;
  }
  if (["inactive", "failed"].includes(state.trim())) return false;
  return null;
}

/** Ignore unrelated share files and fail closed on inaccessible backup data. */
export async function archiveSourceNeedsCopy(
  source: string,
  lastSuccessAt: string | null,
  run: CommandRunner = runCommand,
): Promise<boolean | null> {
  try {
    const entries = await fs.readdir(/* turbopackIgnore: true */ source, {
      withFileTypes: true,
    });
    const bundles = entries.filter((entry) =>
      entry.isDirectory() && /\.(sparsebundle|backupbundle)$/.test(entry.name));
    if (bundles.length === 0) return false;
    if (lastSuccessAt === null) return true;
    const completedAt = Date.parse(lastSuccessAt);
    if (!Number.isFinite(completedAt)) return null;
    for (const bundle of bundles) {
      // Match the archive worker's ctime guard, including restored old-mtime files.
      const changed = await run("find", [
        path.join(source, bundle.name), "-newerct", new Date(completedAt).toISOString(),
        "-print", "-quit",
      ], 3_000);
      if (changed === null) return null;
      if (changed.length > 0) return true;
    }
    return false;
  } catch {
    return null;
  }
}

function cachedSourceNeedsCopy(source: string, completedAt: string | null) {
  const key = JSON.stringify([source, completedAt]);
  const cached = sourceProbes.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.result;
  const result = archiveSourceNeedsCopy(source, completedAt);
  sourceProbes.set(key, { expiresAt: Date.now() + 30_000, result });
  if (sourceProbes.size > 8) sourceProbes.delete(sourceProbes.keys().next().value!);
  return result;
}

/** Worker activity stays uncached: success can precede verification and cleanup. */
export async function readArchiveCoordination(): Promise<ArchiveCoordination> {
  const status = await readArchiveBackupStatus();
  const details = {
    configured: status.configured,
    state: status.state,
    lastAttemptAt: status.lastAttemptAt,
    lastSuccessAt: status.lastSuccessAt,
    lastSnapshotId: status.lastSnapshotId,
  };
  if (!status.configured) {
    return { ...details, serviceActive: false, needsArchive: false };
  }
  if (process.platform !== "linux") {
    return { ...details, serviceActive: null, needsArchive: null };
  }
  const [worker, needsArchive] = await Promise.all([
    runCommand("systemctl", [
      "show", "pirouter-archive-backup.service", "--property=ActiveState", "--value",
    ], 2_000),
    cachedSourceNeedsCopy(process.env.TIME_MACHINE_PATH?.trim() || "/srv/timemachine",
      status.lastSuccessAt),
  ]);
  return { ...details, serviceActive: archiveWorkerActive(worker), needsArchive };
}
