"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Button,
  Modal,
  Progress,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowDown,
  faArrowUp,
  faArrowsRotate,
  faBolt,
  faCloudArrowUp,
  faCompress,
  faExpand,
  faRoute,
  faTriangleExclamation,
} from "@fortawesome/free-solid-svg-icons";
import type {
  PiStatus,
  SpeedTestJobStatus,
  SpeedTestPhase,
} from "@/lib/types";
import {
  NAV_ITEMS,
  type DashboardView,
} from "@/components/dashboard/config";
import { StateDot } from "@/components/dashboard/primitives";

function Clock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let intervalId: number | undefined;
    const untilNextMinute = 60_000 - (Date.now() % 60_000);
    const timeoutId = window.setTimeout(() => {
      setNow(new Date());
      intervalId = window.setInterval(() => setNow(new Date()), 60_000);
    }, untilNextMinute);

    return () => {
      window.clearTimeout(timeoutId);
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, []);

  const clock = useMemo(
    () => ({
      time: now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      date: now.toLocaleDateString([], {
        weekday: "short",
        month: "short",
        day: "numeric",
      }),
    }),
    [now],
  );

  return (
    <div className="clockBlock">
      <strong>{clock.time}</strong>
      <small>{clock.date}</small>
    </div>
  );
}

export function DashboardHeader({
  status,
  refreshing,
  portableExportStarting,
  onRefresh,
  onPortableExport,
  onEnterFocusMode,
}: {
  status: PiStatus;
  refreshing: boolean;
  portableExportStarting: boolean;
  onRefresh: () => void;
  onPortableExport: () => void;
  onEnterFocusMode: () => void;
}) {
  return (
    <header className="topBar">
      <div className="brandBlock">
        <span className="brandMark">
          <FontAwesomeIcon icon={faRoute} fixedWidth />
        </span>
        <span>
          <strong>{status.identity.name}</strong>
          <small>{status.identity.location}</small>
        </span>
      </div>
      <div className={`globalState ${status.summary.level}`}>
        <StateDot level={status.summary.level} />
        <span>{status.summary.message}</span>
      </div>
      <div className="topActions">
        <Clock />
        <Tooltip label="Refresh now">
          <ActionIcon
            variant="subtle"
            size="xl"
            aria-label="Refresh status"
            onClick={onRefresh}
            loading={refreshing}
          >
            <FontAwesomeIcon icon={faArrowsRotate} fixedWidth />
          </ActionIcon>
        </Tooltip>
        <Tooltip label="Export latest backup">
          <ActionIcon
            variant="subtle"
            size="xl"
            aria-label="Export latest Time Machine snapshot"
            onClick={onPortableExport}
            loading={portableExportStarting}
            disabled={!status.portableExport.configured}
          >
            <FontAwesomeIcon icon={faCloudArrowUp} fixedWidth />
          </ActionIcon>
        </Tooltip>
        <Tooltip label="Focus mode">
          <ActionIcon
            className="focusModeAction"
            variant="subtle"
            size="xl"
            aria-label="Hide dashboard controls"
            onClick={onEnterFocusMode}
          >
            <FontAwesomeIcon icon={faExpand} fixedWidth />
          </ActionIcon>
        </Tooltip>
      </div>
    </header>
  );
}

export function DashboardNavigation({
  view,
  onChange,
  onEnterFocusMode,
}: {
  view: DashboardView;
  onChange: (view: DashboardView) => void;
  onEnterFocusMode: () => void;
}) {
  return (
    <nav className="bottomNav" aria-label="Dashboard views">
      {NAV_ITEMS.map((item) => (
        <UnstyledButton
          key={item.id}
          className={view === item.id ? "active" : ""}
          onClick={() => onChange(item.id)}
          aria-current={view === item.id ? "page" : undefined}
        >
          <FontAwesomeIcon icon={item.icon} fixedWidth />
          <span>{item.label}</span>
        </UnstyledButton>
      ))}
      <UnstyledButton
        className="focusModeButton"
        onClick={onEnterFocusMode}
        aria-label="Hide dashboard controls"
      >
        <FontAwesomeIcon icon={faExpand} fixedWidth />
        <span>Focus</span>
      </UnstyledButton>
    </nav>
  );
}

export function DashboardChromeRestore({
  onRestore,
}: {
  onRestore: () => void;
}) {
  return (
    <UnstyledButton
      className="chromeRestoreButton"
      onClick={onRestore}
      aria-label="Show dashboard header and navigation"
    >
      <FontAwesomeIcon icon={faCompress} fixedWidth />
      <span>Show controls</span>
    </UnstyledButton>
  );
}

export function SpeedTestModal({
  opened,
  job,
  onRun,
  onClose,
}: {
  opened: boolean;
  job: SpeedTestJobStatus;
  onRun: () => void;
  onClose: () => void;
}) {
  const result = job.result;
  const stages: Array<{ phase: SpeedTestPhase; label: string }> = [
    { phase: "latency", label: "Latency" },
    { phase: "download", label: "Download" },
    { phase: "upload", label: "Upload" },
  ];
  const activePhase = job.phase === "selecting" ? "latency" : job.phase;
  const activeIndex = stages.findIndex((stage) => stage.phase === activePhase);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Internet speed test"
      centered
      size="sm"
      closeButtonProps={{ "aria-label": "Close speed test" }}
      classNames={{ content: "speedModal", header: "speedModalHeader" }}
    >
      {job.state === "running" ? (
        <div className="speedProgress" role="status" aria-live="polite">
          <div className="speedProgressHead">
            <div>
              <small>TEST IN PROGRESS</small>
              <strong>{job.message}</strong>
            </div>
            <span>
              {job.progressPct}% · {job.elapsedSeconds}s
            </span>
          </div>
          <Progress
            value={job.progressPct}
            color="teal"
            radius="xl"
            size="md"
            aria-label={`Speed test ${job.progressPct}% complete`}
          />
          <div className="speedStages" aria-label="Test stages">
            {stages.map((stage, index) => {
              const state =
                index < activeIndex
                  ? "done"
                  : index === activeIndex
                    ? "active"
                    : "waiting";
              return (
                <div className={state} key={stage.phase}>
                  <i aria-hidden="true" />
                  <span>{stage.label}</span>
                </div>
              );
            })}
          </div>
          <div className="speedLive">
            <div>
              <FontAwesomeIcon icon={faBolt} fixedWidth />
              <strong>{job.live.latencyMs ?? "—"}</strong>
              <span>ms latency</span>
            </div>
            <div>
              <FontAwesomeIcon icon={faArrowDown} fixedWidth />
              <strong>{job.live.downloadMbps ?? "—"}</strong>
              <span>Mbps down</span>
            </div>
            <div>
              <FontAwesomeIcon icon={faArrowUp} fixedWidth />
              <strong>{job.live.uploadMbps ?? "—"}</strong>
              <span>Mbps up</span>
            </div>
          </div>
          <p className="speedHint">
            You can close this window; the test will continue.
          </p>
        </div>
      ) : job.state === "error" ? (
        <div className="speedLoading error">
          <FontAwesomeIcon icon={faTriangleExclamation} fixedWidth />
          <strong>Speed test unavailable</strong>
          <p>{job.error}</p>
          <Button color="teal" variant="light" onClick={onRun}>
            Try again
          </Button>
        </div>
      ) : result ? (
        <div className="speedComplete" role="status">
          <div className="speedResults">
            <div>
              <FontAwesomeIcon icon={faArrowDown} fixedWidth />
              <strong>{result.downloadMbps}</strong>
              <span>Mbps down</span>
            </div>
            <div>
              <FontAwesomeIcon icon={faArrowUp} fixedWidth />
              <strong>{result.uploadMbps}</strong>
              <span>Mbps up</span>
            </div>
            <div>
              <FontAwesomeIcon icon={faBolt} fixedWidth />
              <strong>{result.latencyMs}</strong>
              <span>ms latency</span>
            </div>
            <p>{[result.isp, result.serverName].filter(Boolean).join(" · ")}</p>
          </div>
          <Button color="teal" variant="light" fullWidth onClick={onRun}>
            Run again
          </Button>
        </div>
      ) : (
        <div className="speedLoading">
          <FontAwesomeIcon icon={faBolt} fixedWidth />
          <strong>Ready to test your connection</strong>
          <p>The test usually takes less than a minute.</p>
          <Button color="teal" variant="light" onClick={onRun}>
            Start speed test
          </Button>
        </div>
      )}
    </Modal>
  );
}
