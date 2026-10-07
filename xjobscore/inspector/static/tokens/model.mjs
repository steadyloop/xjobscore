const HEARTBEAT_STATES = {
  healthy: { label: "Healthy", tone: "healthy" },
  running: { label: "Running", tone: "running" },
  queued: { label: "Queued", tone: "queued" },
  paused: { label: "Paused", tone: "paused" },
  offline: { label: "Offline", tone: "offline" },
  attention: { label: "Attention", tone: "attention" },
  never: { label: "Never run", tone: "never" },
};

export function heartbeatPresentation(state) {
  return HEARTBEAT_STATES[state] ?? HEARTBEAT_STATES.offline;
}

export function tokenPromotable(row) {
  return row.status === "live"
    && row.polling_supported !== false
    && !row.muted
    && row.feed_state === "none"
    && row.category === "ATS";
}

/* Validate-unknown sweep ---------------------------------------------------- */

/* 9,812 -> "9.8k"; small numbers stay exact so a 100-token sweep reads true. */
export function compactCount(value) {
  if (value == null) return "—";
  if (value < 1000) return String(value);
  if (value < 100_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${Math.round(value / 1000)}k`;
}

/* 907 -> "15m", 75 -> "1m 15s", 3725 -> "1h 2m". */
export function shortDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const rest = s % 60;
    return m < 10 && rest ? `${m}m ${rest}s` : `${m}m`;
  }
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/* 222 -> "3:42", for a cooldown counting down in place. */
export function clock(seconds) {
  const s = Math.max(0, Math.round(seconds ?? 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const ATTENTION = { failing: 0, cooling: 1 };

/* Vendor rows in the order that answers "what needs me": failing, then
   cooling (soonest back first), then running (most left first). Done vendors
   collapse to names only -- a row per finished vendor is the noise this
   panel exists to avoid. */
export function sweepVendorRows(vendors = []) {
  const done = vendors.filter((v) => v.state === "done").map((v) => v.ats).sort();
  const active = vendors
    .filter((v) => v.state !== "done")
    .sort((a, b) => {
      const rank = (v) => ATTENTION[v.state] ?? 2;
      if (rank(a) !== rank(b)) return rank(a) - rank(b);
      if (a.state === "cooling") return a.cooling_s - b.cooling_s;
      return (b.total - b.done) - (a.total - a.done) || a.ats.localeCompare(b.ats);
    });
  return { active, done };
}

export function sweepActive(snapshot) {
  return snapshot?.status === "running" || snapshot?.status === "paused";
}

/* The one headline: where are we, how fast, how long. */
export function sweepRate(rate) {
  if (rate > 0 && rate < 1) return `${rate.toFixed(2).replace(/0$/, "")}/s`;
  return `${Math.round(rate)}/s`;
}

export function sweepHeadline(snapshot) {
  const pct = snapshot.total ? Math.floor((100 * snapshot.done) / snapshot.total) : 0;
  const parts = [`${pct}%`];
  if (snapshot.status === "paused") parts.push("paused");
  else if (snapshot.rate > 0) parts.push(sweepRate(snapshot.rate));
  if (snapshot.eta_s != null && snapshot.status === "running") parts.push(`~${shortDuration(snapshot.eta_s)} left`);
  return { pct, text: parts.join(" · ") };
}
