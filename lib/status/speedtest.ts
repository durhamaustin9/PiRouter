import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import type {
  SpeedTestJobStatus,
  SpeedTestResult,
  SpeedTestStartResponse,
} from "@/lib/types";
import { round } from "@/lib/status/shared";

const SPEED_TEST_TIMEOUT_MS = 90_000;

type SpeedTestStore = {
  status: SpeedTestJobStatus;
  process: ChildProcess | null;
};

type SpeedTestEvent = {
  type?: "testStart" | "ping" | "download" | "upload" | "result";
  ping?: { latency?: number; progress?: number };
  download?: { bandwidth?: number; progress?: number };
  upload?: { bandwidth?: number; progress?: number };
  packetLoss?: number;
  server?: { name?: string; location?: string };
  isp?: string;
  timestamp?: string;
};

const idleStatus = (): SpeedTestJobStatus => ({
  jobId: null,
  state: "idle",
  phase: "idle",
  progressPct: 0,
  message: "Ready to test",
  startedAt: null,
  completedAt: null,
  elapsedSeconds: 0,
  live: { latencyMs: null, downloadMbps: null, uploadMbps: null },
  result: null,
  error: null,
});

const speedTestGlobal = globalThis as typeof globalThis & {
  __piRouterSpeedTest?: SpeedTestStore;
};

const store =
  speedTestGlobal.__piRouterSpeedTest ??
  (speedTestGlobal.__piRouterSpeedTest = {
    status: idleStatus(),
    process: null,
  });

function progress(value: number | undefined, start: number, span: number) {
  const normalized = Math.min(1, Math.max(0, value ?? 0));
  return Math.round(start + normalized * span);
}

function bandwidthMbps(bytesPerSecond: number | undefined) {
  if (bytesPerSecond === undefined) return null;
  return round((bytesPerSecond * 8) / 1_000_000, 2);
}

function resultFromEvent(event: SpeedTestEvent): SpeedTestResult {
  return {
    downloadMbps: bandwidthMbps(event.download?.bandwidth) ?? 0,
    uploadMbps: bandwidthMbps(event.upload?.bandwidth) ?? 0,
    latencyMs: round(event.ping?.latency ?? 0, 2),
    packetLossPct: event.packetLoss ?? null,
    serverName: event.server
      ? [event.server.name, event.server.location].filter(Boolean).join(" · ")
      : null,
    isp: event.isp ?? null,
    capturedAt: event.timestamp ?? new Date().toISOString(),
  };
}

function updateFromEvent(event: SpeedTestEvent) {
  if (store.status.state !== "running") return;

  switch (event.type) {
    case "testStart":
      store.status = {
        ...store.status,
        phase: "selecting",
        progressPct: 3,
        message: "Server selected · measuring latency",
      };
      break;
    case "ping":
      store.status = {
        ...store.status,
        phase: "latency",
        progressPct: progress(event.ping?.progress, 5, 10),
        message: "Measuring latency",
        live: {
          ...store.status.live,
          latencyMs:
            event.ping?.latency === undefined
              ? store.status.live.latencyMs
              : round(event.ping.latency, 2),
        },
      };
      break;
    case "download":
      store.status = {
        ...store.status,
        phase: "download",
        progressPct: progress(event.download?.progress, 15, 55),
        message: "Measuring download speed",
        live: {
          ...store.status.live,
          downloadMbps:
            bandwidthMbps(event.download?.bandwidth) ??
            store.status.live.downloadMbps,
        },
      };
      break;
    case "upload":
      store.status = {
        ...store.status,
        phase: "upload",
        progressPct: progress(event.upload?.progress, 70, 29),
        message: "Measuring upload speed",
        live: {
          ...store.status.live,
          uploadMbps:
            bandwidthMbps(event.upload?.bandwidth) ?? store.status.live.uploadMbps,
        },
      };
      break;
    case "result": {
      const result = resultFromEvent(event);
      store.status = {
        ...store.status,
        state: "complete",
        phase: "complete",
        progressPct: 100,
        message: "Speed test complete",
        completedAt: new Date().toISOString(),
        live: {
          latencyMs: result.latencyMs,
          downloadMbps: result.downloadMbps,
          uploadMbps: result.uploadMbps,
        },
        result,
      };
      break;
    }
  }
}

function failTest(message: string) {
  if (store.status.state !== "running") return;
  store.status = {
    ...store.status,
    state: "error",
    phase: "error",
    message: "Speed test failed",
    completedAt: new Date().toISOString(),
    error: message,
  };
}

function parseOutputLine(line: string) {
  const trimmed = line.trim();
  if (!trimmed) return;
  try {
    updateFromEvent(JSON.parse(trimmed) as SpeedTestEvent);
  } catch {
    // Ignore non-JSON CLI notices; stderr is retained for actionable failures.
  }
}

export function getSpeedTestJobStatus(): SpeedTestJobStatus {
  const status = store.status;
  const endTime =
    status.state === "running"
      ? Date.now()
      : status.completedAt
        ? Date.parse(status.completedAt)
        : null;
  const elapsedSeconds =
    status.startedAt && endTime
      ? Math.max(0, Math.floor((endTime - Date.parse(status.startedAt)) / 1_000))
      : 0;

  return {
    ...status,
    elapsedSeconds,
    live: { ...status.live },
    result: status.result ? { ...status.result } : null,
  };
}

export function startSpeedTestJob(): SpeedTestStartResponse {
  if (store.process !== null || store.status.state === "running") {
    return { started: false, job: getSpeedTestJobStatus() };
  }

  store.status = {
    ...idleStatus(),
    jobId: randomUUID(),
    state: "running",
    phase: "selecting",
    progressPct: 1,
    message: "Selecting a nearby test server",
    startedAt: new Date().toISOString(),
  };

  const child = spawn(
    "speedtest",
    [
      "--accept-license",
      "--accept-gdpr",
      "--format=json",
      "--progress=yes",
      "--progress-update-interval=750",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  store.process = child;

  let stdoutBuffer = "";
  let stderr = "";
  const timeout = setTimeout(() => {
    failTest("The speed test timed out after 90 seconds");
    child.kill("SIGTERM");
  }, SPEED_TEST_TIMEOUT_MS);

  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => {
    stdoutBuffer += chunk;
    const lines = stdoutBuffer.split(/\r?\n/);
    stdoutBuffer = lines.pop() ?? "";
    lines.forEach(parseOutputLine);
  });
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk: string) => {
    stderr = `${stderr}${chunk}`.slice(-1_500);
  });
  child.on("error", (error) => {
    clearTimeout(timeout);
    if (store.process === child) store.process = null;
    const systemError = error as NodeJS.ErrnoException;
    failTest(
      systemError.code === "ENOENT"
        ? "Ookla Speedtest CLI is not installed on this Pi"
        : error.message,
    );
  });
  child.on("close", (code) => {
    clearTimeout(timeout);
    if (store.process === child) store.process = null;
    parseOutputLine(stdoutBuffer);
    if (store.status.state === "running") {
      const detail = stderr.trim().split(/\r?\n/).filter(Boolean).at(-1);
      failTest(
        detail ||
          (code === 0
            ? "The speed test finished without returning a result"
            : `Ookla Speedtest exited with code ${code ?? "unknown"}`),
      );
    }
  });

  return { started: true, job: getSpeedTestJobStatus() };
}
