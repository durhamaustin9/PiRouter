import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type { StatusIssue } from "@/lib/types";

type Occurrence = { signature: string; firstSeenAt: string; dismissedUntil?: string };
type IssueState = Record<string, Occurrence>;
let queue: Promise<unknown> = Promise.resolve();

function statePath() {
  return process.env.ISSUE_STATUS_PATH?.trim() || path.join(process.cwd(), "data", "issues.json");
}

async function readState(target: string): Promise<IssueState> {
  let contents: string;
  try {
    contents = await fs.readFile(/* turbopackIgnore: true */ target, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return {};
    throw error;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(contents);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    // Preserve the broken file for diagnosis, then let fresh observations
    // restore active alerts rather than disabling every dashboard reading.
    const backup = `${target}.corrupt-${randomUUID()}`;
    await fs.rename(target, backup);
    console.error(`Recovered invalid alert history; previous contents kept at ${backup}`);
    return {};
  }
  const state: IssueState = Object.create(null);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return state;
  for (const [id, value] of Object.entries(parsed)) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const occurrence = value as Partial<Occurrence>;
    if (typeof occurrence.signature !== "string" ||
      typeof occurrence.firstSeenAt !== "string" ||
      !Number.isFinite(Date.parse(occurrence.firstSeenAt))) continue;
    state[id] = {
      signature: occurrence.signature,
      firstSeenAt: occurrence.firstSeenAt,
      ...(typeof occurrence.dismissedUntil === "string" && Number.isFinite(Date.parse(occurrence.dismissedUntil))
        ? { dismissedUntil: occurrence.dismissedUntil } : {}),
    };
  }
  return state;
}

function mutate<T>(operation: (state: IssueState) => T) {
  const result = queue.then(async () => {
    const target = statePath();
    const state = await readState(target);
    const before = JSON.stringify(state);
    const value = operation(state);
    if (before !== JSON.stringify(state)) {
      await fs.mkdir(path.dirname(target), { recursive: true });
      const temporary = `${target}.${randomUUID()}.tmp`;
      try {
        await fs.writeFile(temporary, JSON.stringify(state), { mode: 0o600 });
        await fs.rename(temporary, target);
      } finally {
        await fs.rm(temporary, { force: true }).catch(() => undefined);
      }
    }
    return value;
  });
  queue = result.catch(() => undefined);
  return result;
}

/** Recovery ends an occurrence; a changed failure or severity creates a new one. */
export function reconcileIssues(issues: StatusIssue[], now = Date.now()) {
  return mutate((state) => {
    for (const id of Object.keys(state)) {
      if (!issues.some((issue) => issue.id === id)) delete state[id];
    }
    const active: StatusIssue[] = [];
    const dismissed: StatusIssue[] = [];
    for (const issue of issues) {
      const signature = JSON.stringify([issue.level, issue.message, ...(issue.fingerprint ? [issue.fingerprint] : [])]);
      if (state[issue.id]?.signature !== signature) {
        state[issue.id] = { signature, firstSeenAt: new Date(now).toISOString() };
      }
      const occurrence = state[issue.id];
      const hidden = Date.parse(occurrence.dismissedUntil ?? "") > now;
      if (!hidden) delete occurrence.dismissedUntil;
      (hidden ? dismissed : active).push({ ...issue, firstSeenAt: occurrence.firstSeenAt, dismissedUntil: occurrence.dismissedUntil });
    }
    return { issues: active, dismissedIssues: dismissed };
  }).catch((error: unknown) => {
    console.error("Unable to persist alert history", error);
    // Keep live telemetry and genuine failures visible when the disk cannot
    // save dismissals. A later successful write restores normal operation.
    return { issues: [...issues, {
      id: "alert-history",
      level: "warning" as const,
      message: "Alert history is unavailable",
      detail: "Live readings remain available. Alert dismissals cannot be saved; check the Pi's storage and dashboard service log.",
      scoreImpact: 0,
      firstSeenAt: new Date(now).toISOString(),
    }], dismissedIssues: [] as StatusIssue[] };
  });
}

export function dismissIssue(id: string, firstSeenAt: string, hours: number, now = Date.now()) {
  return mutate((state) => {
    if (!state[id] || state[id].firstSeenAt !== firstSeenAt) {
      throw new Error("This alert changed. Refresh and review the current alert.");
    }
    if (hours === 0) delete state[id].dismissedUntil;
    else state[id].dismissedUntil = new Date(now + hours * 3_600_000).toISOString();
  });
}
