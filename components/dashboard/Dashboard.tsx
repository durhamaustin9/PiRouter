"use client";

import { useEffect, useState } from "react";
import { Button, Loader } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faRoute,
  faTriangleExclamation,
} from "@fortawesome/free-solid-svg-icons";
import type { DashboardView } from "@/components/dashboard/config";
import {
  DashboardHeader,
  DashboardChromeRestore,
  DashboardNavigation,
  SpeedTestModal,
} from "@/components/dashboard/DashboardChrome";
import { useDashboardController } from "@/components/dashboard/useDashboardController";
import { useTouchScroll } from "@/components/dashboard/useTouchScroll";
import { ComponentDetailModal } from "@/components/dashboard/ComponentDetailModal";
import {
  buildComponentDetail,
  type ComponentDetailId,
} from "@/components/dashboard/componentDetails";
import { NetworkView } from "@/components/dashboard/views/NetworkView";
import { OverviewView } from "@/components/dashboard/views/OverviewView";
import { ServicesView } from "@/components/dashboard/views/ServicesView";
import { SystemView } from "@/components/dashboard/views/SystemView";

export function Dashboard() {
  const [view, setView] = useState<DashboardView>("overview");
  const [componentDetailId, setComponentDetailId] =
    useState<ComponentDetailId | null>(null);
  const [focusMode, setFocusMode] = useState(false);
  const controller = useDashboardController();
  const { scrollRef, scrollHandlers } = useTouchScroll(view);

  useEffect(() => {
    if (!focusMode) return;

    const restoreOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocusMode(false);
    };
    window.addEventListener("keydown", restoreOnEscape);
    return () => window.removeEventListener("keydown", restoreOnEscape);
  }, [focusMode]);

  if (controller.loading && !controller.status) {
    return (
      <main className="loadingScreen">
        <div className="brandMark">
          <FontAwesomeIcon icon={faRoute} fixedWidth />
        </div>
        <h1>PiRouter</h1>
        <Loader color="teal" type="dots" />
        <p>Reading network and system health…</p>
      </main>
    );
  }

  if (!controller.status) {
    return (
      <main className="loadingScreen errorScreen">
        <FontAwesomeIcon icon={faTriangleExclamation} fixedWidth />
        <h1>Status service unavailable</h1>
        <p>{controller.error}</p>
        <Button onClick={() => void controller.loadStatus(true)}>Try again</Button>
      </main>
    );
  }

  const status = controller.status;
  const componentDetail = componentDetailId
    ? buildComponentDetail(componentDetailId, {
        status,
        history: controller.history,
        speedTestJob: controller.speedTestJob,
      })
    : null;

  return (
    <div className={`dashboardShell${focusMode ? " focusMode" : ""}`}>
      <DashboardHeader
        status={status}
        refreshing={controller.refreshing}
        portableExportStarting={controller.portableExportStarting}
        onRefresh={() => void controller.loadStatus(true)}
        onPortableExport={() => void controller.runPortableExport()}
        onEnterFocusMode={() => setFocusMode(true)}
      />

      {controller.error && (
        <div className="connectionBanner">
          <FontAwesomeIcon icon={faTriangleExclamation} fixedWidth />
          Showing the last good update · {controller.error}
        </div>
      )}

      <main className="dashboardMain" ref={scrollRef} {...scrollHandlers}>
        {view === "overview" && (
          <OverviewView status={status} onShowDetails={setComponentDetailId} />
        )}
        {view === "network" && (
          <NetworkView
            status={status}
            history={controller.history}
            onSpeedTest={() => void controller.runSpeedTest()}
            speedTestJob={controller.speedTestJob}
            speedTestStarting={controller.speedTestStarting}
            onShowDetails={setComponentDetailId}
          />
        )}
        {view === "services" && (
          <ServicesView
            status={status}
            onPortableExport={() => void controller.runPortableExport()}
            portableExportStarting={controller.portableExportStarting}
            portableExportError={controller.portableExportError}
            onShowDetails={setComponentDetailId}
          />
        )}
        {view === "system" && (
          <SystemView
            status={status}
            history={controller.history}
            onShowDetails={setComponentDetailId}
          />
        )}
      </main>

      <DashboardNavigation
        view={view}
        onChange={setView}
        onEnterFocusMode={() => setFocusMode(true)}
      />

      {focusMode && (
        <DashboardChromeRestore onRestore={() => setFocusMode(false)} />
      )}

      <SpeedTestModal
        opened={controller.speedModalOpen}
        job={controller.speedTestJob}
        starting={controller.speedTestStarting}
        onRun={() => void controller.runSpeedTest()}
        onClose={() => controller.setSpeedModalOpen(false)}
      />

      <ComponentDetailModal
        detail={componentDetail}
        opened={componentDetailId !== null}
        onClose={() => setComponentDetailId(null)}
      />
    </div>
  );
}
