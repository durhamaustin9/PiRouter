import { Progress, RingProgress, UnstyledButton } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowDown,
  faArrowUp,
  faCheck,
  faClockRotateLeft,
  faCloudArrowUp,
  faCircleInfo,
  faGaugeHigh,
  faGlobe,
  faHardDrive,
  faLink,
  faMemory,
  faMicrochip,
  faNetworkWired,
  faPlugCircleXmark,
  faRoute,
  faShieldHalved,
  faTemperatureHalf,
} from "@fortawesome/free-solid-svg-icons";
import type { PiStatus, StatusLevel } from "@/lib/types";
import type { ComponentDetailId } from "@/components/dashboard/componentDetails";
import {
  formatBytes,
  formatRate,
  formatUptime,
  statusColor,
  systemLevel,
  timeMachineDisplay,
} from "@/components/dashboard/formatters";
import {
  Metric,
  Panel,
  PanelHeading,
} from "@/components/dashboard/primitives";

export function OverviewView({
  status,
  onShowDetails,
}: {
  status: PiStatus;
  onShowDetails: (id: ComponentDetailId) => void;
}) {
  const timeMachine = timeMachineDisplay(status.timeMachine);
  const backupLevel: StatusLevel = status.timeMachine.configured
    ? status.timeMachine.status
    : "unconfigured";
  const endpointLevel: StatusLevel = !status.endpoint.configured
    ? "unconfigured"
    : status.endpoint.reachable
      ? "healthy"
      : "critical";
  const tailscaleLevel: StatusLevel = !status.tailscale.installed
    ? "unavailable"
    : status.tailscale.online
      ? "healthy"
      : "critical";

  return (
    <section className="overviewGrid" aria-label="Overview">
      <Panel className="healthPanel">
        <div className="healthRing" data-testid="health-ring">
          <RingProgress
            size={100}
            thickness={7}
            roundCaps
            rootColor="rgba(255,255,255,.06)"
            sections={[
              {
                value: status.summary.score,
                color: statusColor(status.summary.level),
              },
            ]}
            label={
              <strong
                className="healthScore"
                aria-label={`${status.summary.score} out of 100`}
              >
                {status.summary.score}
              </strong>
            }
          />
        </div>
        <UnstyledButton
          className="healthCopy healthDetailTrigger"
          onClick={() => onShowDetails("overall-health")}
          aria-label="Show details for overall health"
          aria-haspopup="dialog"
        >
          <span className="eyebrow">
            Network health
            <FontAwesomeIcon icon={faCircleInfo} fixedWidth />
          </span>
          <h2>{status.summary.message}</h2>
          <p>
            {status.issues[0]?.message ??
              `Checked ${new Date(status.capturedAt).toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
              })}`}
          </p>
        </UnstyledButton>
      </Panel>

      <Panel className="networkPanel">
        <PanelHeading
          icon={faNetworkWired}
          label="Live traffic"
          level={status.network.online ? "healthy" : "critical"}
          meta={status.network.interface ?? "No interface"}
          onSelect={() => onShowDetails("network-traffic")}
        />
        <div className="trafficPair">
          <div>
            <FontAwesomeIcon icon={faArrowDown} fixedWidth />
            <span>{formatRate(status.network.downloadBps)}</span>
            <small>download</small>
          </div>
          <div>
            <FontAwesomeIcon icon={faArrowUp} fixedWidth />
            <span>{formatRate(status.network.uploadBps)}</span>
            <small>upload</small>
          </div>
        </div>
        <div className="inlineFacts">
          <span>{status.network.latencyMs ?? "—"} ms</span>
          <span>{status.network.packetLossPct ?? "—"}% loss</span>
          <span>
            {status.network.linkMbps
              ? `${status.network.linkMbps} Mb link`
              : status.network.ipv4 ?? "No IP"}
          </span>
        </div>
      </Panel>

      <Panel>
        <PanelHeading
          icon={faClockRotateLeft}
          label="Time Machine"
          level={backupLevel}
          meta={
            !status.timeMachine.configured
              ? "Setup"
              : status.timeMachine.backupInProgress
                ? "Writing now"
                : "Backup share"
          }
          onSelect={() => onShowDetails("time-machine")}
        />
        <div className="serviceHero">
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
          <div>
            <strong>{timeMachine.headline}</strong>
            <span>{timeMachine.detail}</span>
          </div>
        </div>
        <Progress
          value={status.system.disk.usedPct}
          color={status.system.disk.usedPct >= 90 ? "red" : "teal"}
          size="sm"
          radius="xl"
          aria-label="Backup disk usage"
        />
        <div className="progressLabel">
          <span>{formatBytes(status.system.disk.usedBytes)} used</span>
          <span>{status.system.disk.usedPct}%</span>
        </div>
      </Panel>

      <Panel>
        <PanelHeading
          icon={faRoute}
          label={status.endpoint.label}
          level={endpointLevel}
          meta={`TCP :${status.endpoint.port}`}
          onSelect={() => onShowDetails("endpoint-route")}
        />
        <div className="serviceHero compact">
          <FontAwesomeIcon
            icon={status.endpoint.reachable ? faLink : faPlugCircleXmark}
            fixedWidth
          />
          <div>
            <strong>
              {!status.endpoint.configured
                ? "Not configured"
                : status.endpoint.reachable
                  ? `${status.endpoint.latencyMs ?? "—"} ms`
                  : "Route offline"}
            </strong>
            <span>{status.endpoint.host ?? "Set ENDPOINT_HOST in .env"}</span>
          </div>
        </div>
        <p className="panelFoot">{status.endpoint.message}</p>
      </Panel>

      <Panel>
        <PanelHeading
          icon={faShieldHalved}
          label="Tailscale"
          level={tailscaleLevel}
          meta={status.tailscale.backendState}
          onSelect={() => onShowDetails("tailscale")}
        />
        <div className="serviceHero compact">
          <FontAwesomeIcon icon={faGlobe} fixedWidth />
          <div>
            <strong>
              {status.tailscale.online
                ? `${status.tailscale.onlinePeerCount} peers online`
                : status.tailscale.installed
                  ? "Disconnected"
                  : "Not installed"}
            </strong>
            <span>{status.tailscale.ip ?? "No tailnet address"}</span>
          </div>
        </div>
        <p className="panelFoot">
          {status.tailscale.directPeerCount} direct · {status.tailscale.peerCount} known
        </p>
      </Panel>

      <Panel>
        <PanelHeading
          icon={faMicrochip}
          label="Pi system"
          level={systemLevel(status.system)}
          meta={formatUptime(status.system.uptimeSeconds)}
          onSelect={() => onShowDetails("pi-system")}
        />
        <div className="systemMetrics">
          <Metric
            label="CPU"
            value={`${status.system.cpuLoadPct}%`}
            icon={faGaugeHigh}
          />
          <Metric
            label="TEMP"
            value={
              status.system.cpuTempC === null
                ? "—"
                : `${status.system.cpuTempC}°`
            }
            icon={faTemperatureHalf}
            tone={(status.system.cpuTempC ?? 0) >= 70 ? "warn" : "good"}
          />
          <Metric
            label="RAM"
            value={`${status.system.memoryPct}%`}
            icon={faMemory}
            tone={status.system.memoryPct >= 90 ? "warn" : "neutral"}
          />
          <Metric
            label="SSD"
            value={`${status.system.disk.usedPct}%`}
            icon={faHardDrive}
            tone={status.system.disk.usedPct >= 90 ? "danger" : "neutral"}
          />
        </div>
      </Panel>
    </section>
  );
}
