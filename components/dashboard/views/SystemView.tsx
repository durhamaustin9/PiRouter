import { Progress } from "@mantine/core";
import {
  faDatabase,
  faGaugeHigh,
  faHardDrive,
  faMemory,
  faMicrochip,
  faTemperatureHalf,
} from "@fortawesome/free-solid-svg-icons";
import type { HistoryPoint, PiStatus } from "@/lib/types";
import type { ComponentDetailId } from "@/components/dashboard/componentDetails";
import {
  formatBytes,
  formatUptime,
  systemLevel,
} from "@/components/dashboard/formatters";
import {
  GaugeRow,
  HistoryBars,
  Panel,
  PanelHeading,
} from "@/components/dashboard/primitives";

export function SystemView({
  status,
  history,
  onShowDetails,
}: {
  status: PiStatus;
  history: HistoryPoint[];
  onShowDetails: (id: ComponentDetailId) => void;
}) {
  const temperaturePercent = status.system.cpuTempC
    ? Math.min(100, (status.system.cpuTempC / 85) * 100)
    : 0;

  return (
    <section className="detailGrid systemDetailGrid" aria-label="System details">
      <Panel className="detailHero systemHealthPanel">
        <PanelHeading
          icon={faMicrochip}
          label="Raspberry Pi 5"
          level={systemLevel(status.system)}
          meta={status.system.hostname}
          onSelect={() => onShowDetails("pi-system")}
        />
        <div className="systemHeroStats">
          <div>
            <span>Uptime</span>
            <strong>{formatUptime(status.system.uptimeSeconds)}</strong>
          </div>
          <div>
            <span>CPU temp</span>
            <strong>
              {status.system.cpuTempC === null
                ? "—"
                : `${status.system.cpuTempC}°C`}
            </strong>
          </div>
          <div>
            <span>Memory</span>
            <strong>{formatBytes(status.system.memoryUsedBytes)}</strong>
          </div>
        </div>
        <div className="gaugeStack">
          <GaugeRow
            icon={faGaugeHigh}
            label="Processor"
            value={status.system.cpuLoadPct}
            detail="1 minute load"
          />
          <GaugeRow
            icon={faMemory}
            label="Memory"
            value={status.system.memoryPct}
            detail={`${formatBytes(status.system.memoryTotalBytes)} installed`}
            color={status.system.memoryPct > 90 ? "red" : "cyan"}
          />
          <GaugeRow
            icon={faTemperatureHalf}
            label="Thermal"
            value={temperaturePercent}
            detail="85°C throttle point"
            color={temperaturePercent > 82 ? "orange" : "teal"}
          />
        </div>
      </Panel>

      <Panel className="storagePanel">
        <PanelHeading
          icon={faHardDrive}
          label="SSD storage"
          level={status.system.disk.usedPct > 90 ? "critical" : "healthy"}
          meta={status.system.disk.mount}
          onSelect={() => onShowDetails("ssd-storage")}
        />
        <div className="diskNumber">
          <strong>
            {formatBytes(
              status.system.disk.totalBytes - status.system.disk.usedBytes,
            )}
          </strong>
          <span>free</span>
        </div>
        <Progress
          value={status.system.disk.usedPct}
          color={status.system.disk.usedPct > 90 ? "red" : "teal"}
          size="lg"
          radius="xl"
        />
        <div className="progressLabel">
          <span>{formatBytes(status.system.disk.usedBytes)} used</span>
          <span>{formatBytes(status.system.disk.totalBytes)} total</span>
        </div>
        <p className="panelFoot truncate">{status.system.disk.filesystem}</p>
      </Panel>

      <Panel className="historyPanel">
        <PanelHeading
          icon={faDatabase}
          label="Health history"
          meta={`${history.length} samples`}
          onSelect={() => onShowDetails("health-history")}
        />
        <HistoryBars history={history} />
        <p className="panelFoot historyFoot">Stored locally · 7-day retention</p>
      </Panel>
    </section>
  );
}
