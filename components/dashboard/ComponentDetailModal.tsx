"use client";

import { Modal } from "@mantine/core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faCircleInfo } from "@fortawesome/free-solid-svg-icons";
import type { ComponentDetail } from "@/components/dashboard/componentDetails";
import { StateDot } from "@/components/dashboard/primitives";

export function ComponentDetailModal({
  detail,
  opened,
  onClose,
}: {
  detail: ComponentDetail | null;
  opened: boolean;
  onClose: () => void;
}) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={detail?.title ?? "Component details"}
      centered
      size="md"
      closeButtonProps={{ "aria-label": "Close component details" }}
      classNames={{
        content: "componentDetailModal",
        header: "componentDetailHeader",
        body: "componentDetailBody",
      }}
    >
      {detail && (
        <div className="componentDetail">
          <div className="componentDetailIntro">
            <span className="componentDetailIcon">
              <FontAwesomeIcon icon={detail.icon} fixedWidth />
            </span>
            <div>
              <span className="detailKicker">
                <StateDot level={detail.level} />
                {detail.kicker}
              </span>
              <p>{detail.description}</p>
            </div>
          </div>

          {detail.progress && (
            <div className="componentProgress">
              <div>
                <span>{detail.progress.label}</span>
                <strong>{detail.progress.value}</strong>
              </div>
              <div
                className="componentProgressTrack"
                role="progressbar"
                aria-label={detail.progress.label}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={detail.progress.percent}
              >
                <i
                  style={{
                    width: `${Math.max(0.35, Math.min(100, detail.progress.percent))}%`,
                  }}
                />
              </div>
              <small>{detail.progress.detail}</small>
            </div>
          )}

          <div className="componentMetricGrid">
            {detail.metrics.map((metric) => (
              <div key={`${metric.label}-${metric.value}`}>
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.hint}</small>
              </div>
            ))}
          </div>

          <div className="componentDetailNote">
            <FontAwesomeIcon icon={faCircleInfo} fixedWidth />
            <span>{detail.note}</span>
          </div>
          <p className="componentDetailUpdated">
            Updated {new Date(detail.updatedAt).toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
              second: "2-digit",
            })}
          </p>
        </div>
      )}
    </Modal>
  );
}
