"use client";

import { useState } from "react";
import { Button, Modal, Select } from "@mantine/core";
import type { PiStatus, StatusIssue } from "@/lib/types";

export function IssuesModal({ opened, onClose, status, onChange }: {
  opened: boolean;
  onClose: () => void;
  status: PiStatus;
  onChange: (issue: StatusIssue, hours: number) => Promise<void>;
}) {
  const [hours, setHours] = useState("24");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dismissed = status.dismissedIssues ?? [];
  async function update(issue: StatusIssue, duration: number) {
    setBusy(issue.id);
    setError(null);
    try { await onChange(issue, duration); }
    catch (error) { setError(error instanceof Error ? error.message : "Unable to update alert"); }
    finally { setBusy(null); }
  }
  function item(issue: StatusIssue, cleared: boolean) {
    return <article className={`alertItem ${issue.level}`} key={issue.id}>
      <strong>{issue.message}</strong>
      <small>{issue.level === "info" ? "Notice" : issue.level === "critical" ? "Error" : "Warning"}{issue.firstSeenAt ? ` · Since ${new Date(issue.firstSeenAt).toLocaleString()}` : ""}</small>
      {issue.detail && <pre>{issue.detail}</pre>}
      {cleared && <p>Cleared until {new Date(issue.dismissedUntil!).toLocaleString()}</p>}
      <Button variant="light" color={cleared ? "gray" : "teal"} disabled={busy !== null} loading={busy === issue.id} onClick={() => void update(issue, cleared ? 0 : Number(hours))}>
        {cleared ? "Show alert again" : "Clear for now"}
      </Button>
    </article>;
  }
  return <Modal opened={opened} onClose={onClose} title="Alerts and details" centered size="lg" closeButtonProps={{ "aria-label": "Close alerts" }} classNames={{ content: "componentDetailModal", header: "componentDetailHeader", body: "componentDetailBody" }}>
    <div className="alertsBody">
      <p>Clearing an alert removes it from the attention count and health score. It can return sooner if it changes or the problem recurs.</p>
      <Select label="Clear alerts for" value={hours} onChange={(value) => setHours(value ?? "24")} data={[{ value: "1", label: "1 hour" }, { value: "24", label: "24 hours" }, { value: "168", label: "7 days" }]} allowDeselect={false} />
      {error && <p role="alert">{error}</p>}
      {status.issues.length ? status.issues.map((issue) => item(issue, false)) : <p>No active alerts.</p>}
      {dismissed.length > 0 && <><h3>Cleared alerts</h3>{dismissed.map((issue) => item(issue, true))}</>}
    </div>
  </Modal>;
}
