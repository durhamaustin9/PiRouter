import { Button } from "@mantine/core";
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
  formatDateTime,
  archiveLevel,
  timeMachineDisplay,
} from "@/components/dashboard/formatters";
import { Panel, PanelHeading } from "@/components/dashboard/primitives";

export function ServicesView({
  status,
  onArchiveBackup,
  archiveStarting,
  archiveError,
  onShowDetails,
}: {
  status: PiStatus;
  onArchiveBackup: () => void;
  archiveStarting: boolean;
  archiveError: string | null;
  onShowDetails: (id: ComponentDetailId) => void;
}) {
  const timeMachine = timeMachineDisplay(status.timeMachine);

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
                  ? "Writing"
                  : "Idle"}
            </strong>
          </div>
        </div>
      </Panel>

      <Panel className="archivePanel">
        <PanelHeading
          icon={faCloudArrowUp}
          label="Secondary archive"
          level={archiveLevel(status.archiveBackup)}
          meta={status.archiveBackup.automatic ? "Automatic" : "Setup"}
          onSelect={() => onShowDetails("archive")}
        />
        <div className="serviceHero compact">
          <FontAwesomeIcon icon={faDatabase} fixedWidth />
          <div>
            <strong>{formatDateTime(status.archiveBackup.lastSuccessAt)}</strong>
            <span>{archiveError ?? status.archiveBackup.message}</span>
          </div>
        </div>
        <Button
          className="backupButton"
          leftSection={<FontAwesomeIcon icon={faCloudArrowUp} fixedWidth />}
          onClick={onArchiveBackup}
          loading={
            archiveStarting ||
            status.archiveBackup.state === "running" ||
            status.archiveBackup.state === "maintenance"
          }
          disabled={!status.archiveBackup.configured}
          color="teal"
          fullWidth
        >
          Archive now
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
