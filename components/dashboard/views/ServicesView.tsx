import { Button, Loader } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faCheck,
  faCircleExclamation,
  faClockRotateLeft,
  faCloudArrowUp,
  faComputer,
  faDatabase,
  faDesktop,
  faGear,
  faRoute,
  faShieldHalved,
  faTriangleExclamation,
} from "@fortawesome/free-solid-svg-icons";
import type { PiStatus } from "@/lib/types";
import type { ComponentDetailId } from "@/components/dashboard/componentDetails";
import {
  archiveBackupLevel,
  formatAge,
  formatBytes,
  formatDateTime,
  formatPercent,
  portableExportContinuation,
  portableExportHeadline,
  portableExportLevel,
  portableExportSummary,
  timeMachineDisplay,
} from "@/components/dashboard/formatters";
import { Panel, PanelHeading } from "@/components/dashboard/primitives";

export function ServicesView({
  status,
  onArchiveBackup,
  archiveBackupStarting,
  archiveBackupError,
  onShowDetails,
  onPortableExport,
  portableExportStarting,
  portableExportError,
}: {
  status: PiStatus;
  onArchiveBackup: () => void;
  archiveBackupStarting: boolean;
  archiveBackupError: string | null;
  onShowDetails: (id: ComponentDetailId) => void;
  onPortableExport: () => void;
  portableExportStarting: boolean;
  portableExportError: string | null;
}) {
  const timeMachine = timeMachineDisplay(status.timeMachine);
  const archiveProgress = status.archiveBackup.progressPercent;
  const archiveRunning =
    status.archiveBackup.state === "running" && archiveProgress !== null;
  const exportContinuation = portableExportContinuation(status.portableExport);
  const archiveActive = ["running", "maintenance"].includes(status.archiveBackup.state);
  const archiveActionLabel = status.archiveBackup.state === "maintenance"
    ? "Archive maintenance"
    : archiveActive ? "Archive in progress" : "Back up to Unraid";
  const exportActive = ["running", "requested"].includes(status.portableExport.state);
  const exportActionLabel = exportContinuation === "archive-handoff"
    ? "Resumes after archive"
    : exportContinuation === "session-budget"
      ? "Continuing automatically"
      : status.portableExport.state === "running"
        ? "Export in progress"
        : status.portableExport.state === "requested"
          ? "Waiting for Mac"
          : status.portableExport.state === "paused"
            ? "Resume export"
            : "Export files to Unraid";

  return (
    <section className="detailGrid servicesGrid" aria-label="Service details">
      <Panel className="detailHero timeMachineDetail">
        <PanelHeading
          icon={faClockRotateLeft}
          label="Time Machine backup service"
          level={
            status.timeMachine.configured
              ? status.timeMachine.status
              : "unconfigured"
          }
          onSelect={() => onShowDetails("time-machine")}
        />
        <div className="serviceStatusLine">
          <div className="largeStateIcon">
            <FontAwesomeIcon
              icon={
                status.timeMachine.backupInProgress
                  ? faCloudArrowUp
                  : status.timeMachine.status === "healthy"
                    ? faCheck
                    : faCloudArrowUp
              }
              fixedWidth
            />
          </div>
          <div>
            <span className="eyebrow">Current status</span>
            <h2>{timeMachine.headline}</h2>
            <p>{timeMachine.detail}</p>
          </div>
        </div>
        <div className="factList horizontalFacts">
          <div>
            <span>Share path</span>
            <strong>{status.timeMachine.path ?? "Not configured"}</strong>
          </div>
          <div>
            <span>SMB service</span>
            <strong>
              {status.timeMachine.serviceActive === null
                ? "Unknown"
                : status.timeMachine.serviceActive
                  ? "Running"
                  : "Stopped"}
            </strong>
          </div>
          <div>
            <span>Share activity</span>
            <strong>
              {status.timeMachine.backupInProgress === null
                ? "Unknown"
                : status.timeMachine.backupInProgress
                  ? "Detected"
                  : "Idle"}
            </strong>
          </div>
        </div>
      </Panel>

      <Panel className="archiveBackupPanel">
        <PanelHeading
          icon={faCloudArrowUp}
          label="Unraid archive"
          level={archiveBackupLevel(status.archiveBackup)}
          meta={status.archiveBackup.automatic ? "Automatic" : "Setup"}
          onSelect={() => onShowDetails("archive-backup")}
        />
        <div className="serviceHero compact">
          <FontAwesomeIcon icon={faDatabase} fixedWidth />
          <div>
            <strong>
              {archiveRunning
                ? `${formatPercent(archiveProgress)} processed`
                : formatDateTime(status.archiveBackup.lastSuccessAt)}
            </strong>
            <span>
              {archiveBackupError ??
                (archiveRunning &&
                status.archiveBackup.bytesProcessed !== null &&
                status.archiveBackup.totalBytes !== null
                  ? `${formatBytes(status.archiveBackup.bytesProcessed)} of ${formatBytes(status.archiveBackup.totalBytes)} · updated ${formatAge(status.archiveBackup.progressUpdatedAt)}`
                  : status.archiveBackup.message)}
            </span>
          </div>
        </div>
        {archiveRunning && (
          <div className="archiveInlineProgress" aria-hidden="true">
            <i
              style={{
                width: `${Math.max(0.35, Math.min(100, archiveProgress))}%`,
              }}
            />
          </div>
        )}
        <Button
          className="backupButton"
          leftSection={archiveActive ? <Loader size={12} color="var(--good)" /> : <FontAwesomeIcon icon={faCloudArrowUp} fixedWidth />}
          onClick={onArchiveBackup}
          loading={archiveBackupStarting}
          disabled={!status.archiveBackup.configured || archiveActive}
          aria-label={archiveActionLabel}
          aria-busy={archiveBackupStarting || archiveActive}
          color="teal"
          fullWidth
        >
          {archiveActionLabel}
        </Button>
      </Panel>

      <Panel className="portableExportPanel">
        <PanelHeading icon={faCloudArrowUp} label="Browsable exports" level={portableExportLevel(status.portableExport)} onSelect={() => onShowDetails("portable-export")} />
        <div className="serviceHero compact"><FontAwesomeIcon icon={faDatabase} fixedWidth /><div>
          <strong>{portableExportHeadline(status.portableExport)}</strong>
          <span>{portableExportError ?? portableExportSummary(status.portableExport)}</span>
        </div></div>
        {status.portableExport.bytesCopied !== null && <div className="inlineFacts"><span>{formatBytes(status.portableExport.bytesCopied)} retained on Unraid</span><span>{(status.portableExport.filesCopied ?? 0).toLocaleString()} files complete</span></div>}
        <Button
          color="teal"
          fullWidth
          onClick={onPortableExport}
          loading={portableExportStarting}
          leftSection={status.portableExport.state === "running" ? <Loader size={12} color="var(--good)" /> : undefined}
          disabled={!status.portableExport.configured || exportActive || exportContinuation !== null}
          aria-label={exportActionLabel}
          aria-busy={portableExportStarting || status.portableExport.state === "running"}
        >
          {exportActionLabel}
        </Button>
      </Panel>

      <Panel className="endpointPanel">
        <PanelHeading
          icon={faRoute}
          label={status.endpoint.label}
          level={
            !status.endpoint.configured
              ? "unconfigured"
              : status.endpoint.reachable
                ? "healthy"
                : "critical"
          }
          onSelect={() => onShowDetails("endpoint-route")}
        />
        <div className="routeDiagram">
          <span>
            <FontAwesomeIcon icon={faComputer} fixedWidth /> PiRouter
          </span>
          <i className={status.endpoint.reachable ? "routeUp" : "routeDown"} />
          <span>
            <FontAwesomeIcon icon={faDesktop} fixedWidth /> Service endpoint
          </span>
        </div>
        <div className="factList compactFacts">
          <div>
            <span>Target</span>
            <strong>
              {status.endpoint.host
                ? `${status.endpoint.host}:${status.endpoint.port}`
                : "Not configured"}
            </strong>
          </div>
          <div>
            <span>TCP probe</span>
            <strong>
              {status.endpoint.reachable === null
                ? "Waiting for setup"
                : status.endpoint.reachable
                  ? `${status.endpoint.latencyMs ?? "—"} ms`
                  : "Failed"}
            </strong>
          </div>
        </div>
      </Panel>

      <Panel className="tailscalePanel">
        <PanelHeading
          icon={faShieldHalved}
          label="Tailscale mesh"
          level={
            !status.tailscale.installed
              ? "unavailable"
              : status.tailscale.online
                ? "healthy"
                : "critical"
          }
          onSelect={() => onShowDetails("tailscale")}
        />
        <div className="qualityReadout three">
          <div>
            <strong>{status.tailscale.onlinePeerCount}</strong>
            <span>online</span>
          </div>
          <div>
            <strong>{status.tailscale.directPeerCount}</strong>
            <span>direct</span>
          </div>
          <div>
            <strong>{status.tailscale.peerCount}</strong>
            <span>known</span>
          </div>
        </div>
        <p className="addressTag">{status.tailscale.ip ?? "No Tailscale IP"}</p>
      </Panel>

      <Panel className="issuePanel">
        <PanelHeading
          icon={faTriangleExclamation}
          label="Attention"
          meta={`${status.issues.length}`}
          onSelect={() => onShowDetails("attention")}
        />
        <div className="issueList">
          {status.issues.length === 0 ? (
            <div className="issue good">
              <FontAwesomeIcon icon={faCheck} fixedWidth />
              <span>Everything looks good</span>
            </div>
          ) : (
            status.issues.map((issue) => (
              <div className={`issue ${issue.level}`} key={issue.id}>
                <FontAwesomeIcon
                  icon={
                    issue.level === "critical"
                      ? faCircleExclamation
                      : faGear
                  }
                  fixedWidth
                />
                <span>{issue.message}</span>
              </div>
            ))
          )}
        </div>
      </Panel>
    </section>
  );
}
