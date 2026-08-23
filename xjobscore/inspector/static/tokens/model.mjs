const HEARTBEAT_STATES = {
  healthy: { label: "Healthy", tone: "healthy" },
  running: { label: "Running", tone: "running" },
  queued: { label: "Queued", tone: "queued" },
  offline: { label: "Offline", tone: "offline" },
  attention: { label: "Attention", tone: "attention" },
  never: { label: "Never run", tone: "never" },
};

export function heartbeatPresentation(state) {
  return HEARTBEAT_STATES[state] ?? HEARTBEAT_STATES.offline;
}

export function tokenPromotable(row) {
  return row.status === "live"
    && !row.muted
    && row.feed_state === "none"
    && row.category === "ATS";
}
