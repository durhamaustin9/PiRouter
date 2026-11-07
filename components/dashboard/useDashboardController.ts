"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  HistoryPoint,
  PiStatus,
  PortableExportStatus,
  SpeedTestJobStatus,
  SpeedTestStartResponse,
  StatusIssue,
} from "@/lib/types";

const STATUS_INTERVAL_MS = 10_000;
const HISTORY_INTERVAL_MS = 60_000;
const SPEED_TEST_INTERVAL_MS = 750;

const INITIAL_SPEED_TEST_JOB: SpeedTestJobStatus = {
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
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(15_000),
    cache: "no-store",
    headers: { accept: "application/json", ...(init?.headers ?? {}) },
  });
  const data = (await response.json().catch(() => null)) as
    | (T & { error?: string })
    | null;
  if (!response.ok) {
    throw new Error(data?.error ?? `Request failed: ${response.status}`);
  }
  if (data === null) throw new Error("The Pi returned an empty response");
  return data;
}

export function useDashboardController() {
  const [status, setStatus] = useState<PiStatus | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speedTestJob, setSpeedTestJob] = useState<SpeedTestJobStatus>(
    INITIAL_SPEED_TEST_JOB,
  );
  const [speedTestStarting, setSpeedTestStarting] = useState(false);
  const [speedModalOpen, setSpeedModalOpen] = useState(false);
  const [archiveBackupStarting, setArchiveBackupStarting] = useState(false);
  const [archiveBackupError, setArchiveBackupError] = useState<string | null>(null);
  const [portableExportStarting, setPortableExportStarting] = useState(false);
  const [portableExportError, setPortableExportError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const statusInFlightRef = useRef<Promise<void> | null>(null);
  const historyInFlightRef = useRef<Promise<void> | null>(null);
  const speedStatusInFlightRef = useRef<Promise<void> | null>(null);
  const speedStartInFlightRef = useRef<Promise<void> | null>(null);
  const archiveStartInFlightRef = useRef(false);
  const exportStartInFlightRef = useRef(false);
  const statusVersionRef = useRef(0);
  const speedVersionRef = useRef(0);

  const loadStatus = useCallback((showSpinner = false) => {
    if (statusInFlightRef.current) return statusInFlightRef.current;
    if (showSpinner) setRefreshing(true);
    const version = statusVersionRef.current;

    const request = (async () => {
      try {
        const nextStatus = await fetchJson<PiStatus>("/api/status");
        if (!mountedRef.current || version !== statusVersionRef.current) return;
        setStatus(nextStatus);
        setError(null);
      } catch (requestError) {
        if (!mountedRef.current || version !== statusVersionRef.current) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Unable to reach the local status service",
        );
      } finally {
        if (mountedRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    })().finally(() => {
      statusInFlightRef.current = null;
    });

    statusInFlightRef.current = request;
    return request;
  }, []);

  const loadHistory = useCallback(() => {
    if (historyInFlightRef.current) return historyInFlightRef.current;

    const request = (async () => {
      try {
        const nextHistory = await fetchJson<HistoryPoint[]>(
          "/api/history?limit=90",
        );
        if (mountedRef.current) setHistory(nextHistory);
      } catch {
        // Live health remains useful when history is temporarily unavailable.
      }
    })().finally(() => {
      historyInFlightRef.current = null;
    });

    historyInFlightRef.current = request;
    return request;
  }, []);

  const loadSpeedTestStatus = useCallback(() => {
    if (speedStatusInFlightRef.current) return speedStatusInFlightRef.current;
    const version = speedVersionRef.current;

    const request = (async () => {
      try {
        const job = await fetchJson<SpeedTestJobStatus>("/api/speedtest");
        if (mountedRef.current && version === speedVersionRef.current) setSpeedTestJob(job);
      } catch {
        // A transient status-poll failure should not hide the running job UI.
      }
    })().finally(() => {
      speedStatusInFlightRef.current = null;
    });

    speedStatusInFlightRef.current = request;
    return request;
  }, []);

  const refreshAfterAction = useCallback(async () => {
    // Wait for any pre-action poll to finish, then request a fresh snapshot.
    await statusInFlightRef.current;
    await loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    mountedRef.current = true;

    const refreshVisibleData = () => {
      if (document.visibilityState !== "visible") return;
      void loadStatus();
      void loadSpeedTestStatus();
    };
    const refreshAllVisibleData = () => {
      refreshVisibleData();
      if (document.visibilityState === "visible") void loadHistory();
    };
    const statusTimer = window.setInterval(
      refreshVisibleData,
      STATUS_INTERVAL_MS,
    );
    const historyTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadHistory();
    }, HISTORY_INTERVAL_MS);

    const initialTimer = window.setTimeout(() => {
      void loadStatus();
      void loadHistory();
      void loadSpeedTestStatus();
    }, 0);
    document.addEventListener("visibilitychange", refreshAllVisibleData);
    window.addEventListener("online", refreshAllVisibleData);

    return () => {
      mountedRef.current = false;
      window.clearTimeout(initialTimer);
      window.clearInterval(statusTimer);
      window.clearInterval(historyTimer);
      document.removeEventListener("visibilitychange", refreshAllVisibleData);
      window.removeEventListener("online", refreshAllVisibleData);
    };
  }, [loadHistory, loadSpeedTestStatus, loadStatus]);

  useEffect(() => {
    if (speedTestJob.state !== "running") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadSpeedTestStatus();
    }, SPEED_TEST_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadSpeedTestStatus, speedTestJob.state]);

  const runSpeedTest = useCallback(() => {
    setSpeedModalOpen(true);
    if (speedTestJob.state === "running" || speedStartInFlightRef.current) {
      return speedStartInFlightRef.current;
    }

    setSpeedTestStarting(true);
    speedVersionRef.current += 1;

    const request = (async () => {
      try {
        const response = await fetchJson<SpeedTestStartResponse>(
          "/api/speedtest",
          { method: "POST", signal: AbortSignal.timeout(15_000) },
        );
        speedVersionRef.current += 1;
        if (mountedRef.current) setSpeedTestJob(response.job);
      } catch (requestError) {
        speedVersionRef.current += 1;
        if (!mountedRef.current) return;
        const now = new Date().toISOString();
        setSpeedTestJob({
          ...INITIAL_SPEED_TEST_JOB,
          state: "error",
          phase: "error",
          message: "Speed test failed",
          completedAt: now,
          error:
            requestError instanceof Error
              ? requestError.message
              : "Unable to start the speed test",
        });
      }
    })().finally(() => {
      speedStartInFlightRef.current = null;
      if (mountedRef.current) setSpeedTestStarting(false);
    });

    speedStartInFlightRef.current = request;
    return request;
  }, [speedTestJob.state]);

  const runArchiveBackup = useCallback(async () => {
    if (archiveStartInFlightRef.current) return;
    archiveStartInFlightRef.current = true;
    statusVersionRef.current += 1;
    setArchiveBackupStarting(true);
    setArchiveBackupError(null);
    try {
      await fetchJson<{ message: string }>("/api/archive-backup", {
        method: "POST",
      });
      statusVersionRef.current += 1;
      await refreshAfterAction();
      window.setTimeout(() => {
        if (mountedRef.current) void loadStatus();
      }, 1_000);
    } catch (requestError) {
      if (!mountedRef.current) return;
      setArchiveBackupError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to start the Unraid archive",
      );
    } finally {
      archiveStartInFlightRef.current = false;
      if (mountedRef.current) setArchiveBackupStarting(false);
    }
  }, [loadStatus, refreshAfterAction]);

  const changeIssue = useCallback(async (issue: StatusIssue, hours: number) => {
    statusVersionRef.current += 1;
    const nextStatus = await fetchJson<PiStatus>("/api/issues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: issue.id, firstSeenAt: issue.firstSeenAt, hours }),
    });
    statusVersionRef.current += 1;
    if (mountedRef.current) {
      setStatus(nextStatus);
      setError(null);
    }
  }, []);

  const runPortableExport = useCallback(async () => {
    if (exportStartInFlightRef.current) return;
    exportStartInFlightRef.current = true;
    statusVersionRef.current += 1;
    setPortableExportStarting(true);
    setPortableExportError(null);
    try {
      const response = await fetchJson<{ status: PortableExportStatus }>("/api/portable-export", { method: "POST" });
      statusVersionRef.current += 1;
      if (mountedRef.current) {
        setStatus((current) => current ? { ...current, portableExport: response.status } : current);
        setError(null);
      }
      await refreshAfterAction();
    } catch (error) {
      if (mountedRef.current) {
        setPortableExportError(error instanceof Error ? error.message : "Unable to request export");
      }
    } finally {
      exportStartInFlightRef.current = false;
      if (mountedRef.current) setPortableExportStarting(false);
    }
  }, [refreshAfterAction]);

  return {
    status,
    history,
    loading,
    refreshing,
    error,
    loadStatus,
    speedTestJob,
    speedTestStarting,
    speedModalOpen,
    setSpeedModalOpen,
    runSpeedTest,
    archiveBackupStarting,
    archiveBackupError,
    runArchiveBackup,
    changeIssue,
    runPortableExport,
    portableExportStarting,
    portableExportError,
    clearActionErrors: () => {
      setArchiveBackupError(null);
      setPortableExportError(null);
    },
  };
}
