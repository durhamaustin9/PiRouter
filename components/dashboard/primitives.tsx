import { Progress, UnstyledButton } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { faCircleInfo } from "@fortawesome/free-solid-svg-icons";
import type { HistoryPoint, StatusLevel } from "@/lib/types";
import { formatRate, statusColor } from "@/components/dashboard/formatters";

export function StateDot({ level }: { level: StatusLevel }) {
  const color = statusColor(level);
  return <span className="stateDot" style={{ backgroundColor: color, color }} />;
}

export function Panel({
  className = "",
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <article className={`panel ${className}`}>{children}</article>;
}

export function PanelHeading({
  icon,
  label,
  level,
  meta,
  onSelect,
}: {
  icon: IconDefinition;
  label: string;
  level?: StatusLevel;
  meta?: string;
  onSelect?: () => void;
}) {
  const content = (
    <>
      <FontAwesomeIcon icon={icon} fixedWidth />
      <span>{label}</span>
      {onSelect && (
        <FontAwesomeIcon
          className="panelInfoIcon"
          icon={faCircleInfo}
          fixedWidth
        />
      )}
    </>
  );

  return (
    <div className="panelHeading">
      {onSelect ? (
        <UnstyledButton
          className="panelHeadingPrimary selectable"
          onClick={onSelect}
          aria-label={`Show details for ${label}`}
          aria-haspopup="dialog"
          title={label}
        >
          {content}
        </UnstyledButton>
      ) : (
        <div className="panelHeadingPrimary">{content}</div>
      )}
      <span className="panelMeta">
        {level && <StateDot level={level} />}
        {meta && <span className="panelMetaLabel" title={meta}>{meta}</span>}
      </span>
    </div>
  );
}

export function Metric({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string;
  icon: IconDefinition;
  tone?: "good" | "warn" | "danger" | "neutral";
}) {
  return (
    <div className="metric">
      <span className={`metricIcon ${tone ?? "neutral"}`}>
        <FontAwesomeIcon icon={icon} fixedWidth />
      </span>
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

export function HistoryBars({ history, metric = "network" }: {
  history: HistoryPoint[];
  metric?: "network" | "cpu";
}) {
  const values = history.slice(-36);
  const max = metric === "cpu" ? 100 : Math.max(
    1,
    ...values.flatMap((point) => [point.downloadBps, point.uploadBps]),
  );
  return (
    <div className="historyBars" role="img" aria-label={metric === "cpu" ? "Recent CPU load" : "Recent network activity: green download, blue upload"}>
      {values.length === 0 ? (
        <div className="emptyChart">History begins after the first minute</div>
      ) : (
        values.map((point) => (
          metric === "cpu" ? (
            <div
              key={point.capturedAt}
              className="historyBar"
              style={{ height: `${Math.max(2, Math.min(100, point.cpuLoadPct))}%` }}
              title={`${point.cpuLoadPct}% CPU load at ${new Date(point.capturedAt).toLocaleTimeString()}`}
            />
          ) : (
            <div className="historyColumn" key={point.capturedAt}
              title={`↓ ${formatRate(point.downloadBps)} · ↑ ${formatRate(point.uploadBps)} at ${new Date(point.capturedAt).toLocaleTimeString()}`}>
              <div className="historyBar" style={{ height: `${Math.max(2, (point.downloadBps / max) * 100)}%` }} />
              <div className="historyBar upload" style={{ height: `${Math.max(2, (point.uploadBps / max) * 100)}%` }} />
            </div>
          )
        ))
      )}
    </div>
  );
}

export function GaugeRow({
  icon,
  label,
  value,
  detail,
  color = "teal",
}: {
  icon: IconDefinition;
  label: string;
  value: number;
  detail: string;
  color?: string;
}) {
  return (
    <div className="gaugeRow">
      <FontAwesomeIcon icon={icon} fixedWidth />
      <div>
        <span>
          <strong>{label}</strong>
          <em>{detail}</em>
        </span>
        <Progress value={value} color={color} size="sm" radius="xl" />
      </div>
      <b>{Math.round(value)}%</b>
    </div>
  );
}
