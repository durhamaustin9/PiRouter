import { Button, Loader } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowDown,
  faArrowTrendUp,
  faArrowUp,
  faBolt,
  faGlobe,
  faWifi,
} from "@fortawesome/free-solid-svg-icons";
import type { HistoryPoint, PiStatus, SpeedTestJobStatus } from "@/lib/types";
import type { ComponentDetailId } from "@/components/dashboard/componentDetails";
import { formatRate } from "@/components/dashboard/formatters";
import {
  HistoryBars,
  Panel,
  PanelHeading,
} from "@/components/dashboard/primitives";

export function NetworkView({
  status,
  history,
  onSpeedTest,
  speedTestJob,
  onShowDetails,
}: {
  status: PiStatus;
  history: HistoryPoint[];
  onSpeedTest: () => void;
  speedTestJob: SpeedTestJobStatus;
  onShowDetails: (id: ComponentDetailId) => void;
}) {
  const networkLevel = status.network.online ? "healthy" : "critical";
  const speedTesting = speedTestJob.state === "running";
  const speedResult = speedTestJob.result;

  return (
    <section
      className="detailGrid networkDetailGrid"
      aria-label="Network details"
    >
      <Panel className="detailHero networkTrafficPanel">
        <PanelHeading
          icon={faArrowTrendUp}
          label="Network throughput"
          level={networkLevel}
          meta="Live"
          onSelect={() => onShowDetails("network-traffic")}
        />
        <div className="largeTraffic">
          <div>
            <small>
              <FontAwesomeIcon icon={faArrowDown} fixedWidth /> Download now
            </small>
            <strong>{formatRate(status.network.downloadBps)}</strong>
          </div>
          <div>
            <small>
              <FontAwesomeIcon icon={faArrowUp} fixedWidth /> Upload now
            </small>
            <strong>{formatRate(status.network.uploadBps)}</strong>
          </div>
        </div>
        <HistoryBars history={history} />
      </Panel>

      <Panel className="linkHealthPanel">
        <PanelHeading
          icon={faWifi}
          label="Link health"
          level={networkLevel}
          onSelect={() => onShowDetails("link-health")}
        />
        <div className="factList compactFacts">
          <div>
            <span>Interface</span>
            <strong>{status.network.interface ?? "—"}</strong>
          </div>
          <div>
            <span>IPv4</span>
            <strong>{status.network.ipv4 ?? "—"}</strong>
          </div>
          <div>
            <span>Negotiated</span>
            <strong>
              {status.network.linkMbps
                ? `${status.network.linkMbps} Mbps`
                : "Unknown"}
            </strong>
          </div>
          <div>
            <span>DNS</span>
            <strong>{status.network.dnsOk ? "Healthy" : "Failed"}</strong>
          </div>
        </div>
      </Panel>

      <Panel className="qualityPanel">
        <PanelHeading
          icon={faGlobe}
          label="Internet quality"
          level={networkLevel}
          meta={status.network.healthTarget}
          onSelect={() => onShowDetails("internet-quality")}
        />
        <div className="qualityReadout">
          <div>
            <strong>{status.network.latencyMs ?? "—"}</strong>
            <span>ms latency</span>
          </div>
          <div>
            <strong>{status.network.packetLossPct ?? "—"}</strong>
            <span>% packet loss</span>
          </div>
        </div>
        <Button
          className="speedButton"
          leftSection={
            speedTesting ? (
              <Loader size={12} color="currentColor" />
            ) : (
              <FontAwesomeIcon icon={faBolt} fixedWidth />
            )
          }
          onClick={onSpeedTest}
          color="teal"
          fullWidth
        >
          {speedTesting ? "View running test" : "Run speed test"}
        </Button>
        <p className="panelFoot speedResultFoot" aria-live="polite">
          {speedTesting
            ? `${speedTestJob.message} · ${speedTestJob.elapsedSeconds}s`
            : speedResult
              ? `Last: ${speedResult.downloadMbps}↓ / ${speedResult.uploadMbps}↑ Mbps`
              : speedTestJob.state === "error"
                ? "Last test failed · Tap to retry"
                : "On-demand · Uses your internet connection"}
        </p>
      </Panel>
    </section>
  );
}
