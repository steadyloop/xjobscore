export const INTERVAL_PRESETS = [
  [300, "5m"],
  [900, "15m"],
  [3600, "1h"],
  [21600, "6h"],
  [86400, "24h"],
];

export const MIN_INTERVAL_S = 60;
export const MAX_INTERVAL_S = 7 * 86_400;
export const ATTENTION_STATES = new Set(["backing_off", "stale", "never_polled"]);

export const STATUS_PRIORITY = {
  backing_off: 0,
  stale: 1,
  never_polled: 2,
  healthy: 3,
  paused: 4,
  disabled: 5,
  unknown: 6,
};

export function formatRequestRate(value) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 1) return value.toFixed(2);
  if (value < 10) return value.toFixed(1).replace(/\.0$/, "");
  return Math.round(value).toLocaleString();
}

export function intervalInput(seconds) {
  const candidates = [
    [86_400, "days"],
    [3_600, "hours"],
    [60, "minutes"],
    [1, "seconds"],
  ];
  for (const [divisor, unit] of candidates) {
    if (seconds && seconds % divisor === 0) {
      return { value: seconds / divisor, unit };
    }
  }
  return { value: seconds ?? "", unit: "seconds" };
}

export function intervalSeconds(value, unit) {
  const number = Number(value);
  const multiplier = { seconds: 1, minutes: 60, hours: 3600, days: 86_400 }[unit];
  if (!Number.isInteger(number) || number <= 0 || !multiplier) return null;
  return number * multiplier;
}

export function intervalError(seconds) {
  if (!Number.isInteger(seconds)) return "Enter a whole-number interval.";
  if (seconds < MIN_INTERVAL_S) return "Interval must be at least 60 seconds.";
  if (seconds > MAX_INTERVAL_S) return "Interval cannot exceed 7 days.";
  return null;
}

export function providerRollups(feeds, errorSources = []) {
  const errors = new Map(errorSources.map((row) => [row.source, row.errors]));
  const providers = new Map();
  for (const feed of feeds) {
    let item = providers.get(feed.source);
    if (!item) {
      item = { source: feed.source, total: 0, attention: 0, paused: 0, requests: 0, errors: 0 };
      providers.set(feed.source, item);
    }
    item.total += 1;
    if (ATTENTION_STATES.has(feed.sched?.status?.state)) item.attention += 1;
    if (feed.policy?.paused_at) {
      item.paused += 1;
    } else {
      item.requests += feed.sched?.requests_per_day ?? 0;
    }
  }
  for (const item of providers.values()) item.errors = errors.get(item.source) ?? 0;
  return [...providers.values()].sort(
    (a, b) => b.total - a.total || a.source.localeCompare(b.source)
  );
}

export function summaryCounts(countsByStatus = {}) {
  const counts = countsByStatus ?? {};
  return {
    ok: counts.healthy ?? 0,
    erroring: counts.backing_off ?? 0,
    paused: counts.paused ?? 0,
    other: (counts.stale ?? 0) + (counts.never_polled ?? 0),
  };
}

/* Next-due presentation and ordering. Paused and unscheduled feeds sort last;
   overdue and near-due feeds are flagged imminent so the UI can highlight them. */
export function dueInfo(dueAt, pausedAt, nowMs) {
  if (pausedAt) return { sortKey: Infinity, text: "—", imminent: false };
  const timestamp = Date.parse(dueAt ?? "");
  if (Number.isNaN(timestamp)) return { sortKey: Infinity, text: "—", imminent: false };
  const remaining = (timestamp - nowMs) / 1000;
  if (remaining <= 0) return { sortKey: timestamp, text: "due", imminent: true };
  let text;
  if (remaining < 90) text = `${Math.round(remaining)}s`;
  else if (remaining < 5400) text = `${Math.round(remaining / 60)}m`;
  else if (remaining < 172_800) text = `${(remaining / 3600).toFixed(1).replace(/\.0$/, "")}h`;
  else text = `${Math.round(remaining / 86_400)}d`;
  return { sortKey: timestamp, text, imminent: remaining <= 60 };
}

export function jobsToday(feed) {
  const spark = feed.spark;
  return Array.isArray(spark) && spark.length ? spark[spark.length - 1] : null;
}

export function matchesFeed(feed, filters) {
  const status = feed.sched.status.state;
  if (filters.view === "issues" && !ATTENTION_STATES.has(status)) return false;
  if (filters.view === "failing" && status !== "backing_off") return false;
  if (filters.view === "paused" && status !== "paused") return false;
  if (filters.provider && feed.source !== filters.provider) return false;
  if (!filters.search) return true;
  const needle = filters.search.toLowerCase();
  return [feed.company_name, feed.source, feed.key]
    .filter(Boolean)
    .some((value) => value.toLowerCase().includes(needle));
}

function nullableNumber(value, missing = Infinity) {
  return value == null || Number.isNaN(Number(value)) ? missing : Number(value);
}

function compareValue(feed, key, nowMs) {
  if (key === "name") return (feed.company_name || feed.key).toLowerCase();
  if (key === "provider") return feed.source;
  if (key === "status") return STATUS_PRIORITY[feed.sched.status.state] ?? 99;
  if (key === "interval") return nullableNumber(feed.policy?.interval_s);
  if (key === "due") {
    return dueInfo(feed.policy?.due_at, feed.policy?.paused_at, nowMs).sortKey;
  }
  if (key === "last_poll") {
    const timestamp = Date.parse(feed.policy?.last_poll_at ?? "");
    return Number.isNaN(timestamp) ? -Infinity : timestamp;
  }
  if (key === "jobs") return nullableNumber(jobsToday(feed), -Infinity);
  if (key === "errors") return nullableNumber(feed.telemetry?.error_count_7d, -Infinity);
  return STATUS_PRIORITY[feed.sched.status.state] ?? 99;
}

export function sortFeeds(feeds, sort = { key: "due", direction: "asc" }, nowMs = 0) {
  const direction = sort.direction === "desc" ? -1 : 1;
  return [...feeds].sort((a, b) => {
    const key = sort.key === "priority" ? "status" : sort.key;
    const left = compareValue(a, key, nowMs);
    const right = compareValue(b, key, nowMs);
    let result = 0;
    if (typeof left === "string") result = left.localeCompare(right);
    else result = left - right;
    if (result) return result * direction;

    if (key === "status") {
      const leftPoll = compareValue(a, "last_poll", nowMs);
      const rightPoll = compareValue(b, "last_poll", nowMs);
      if (leftPoll !== rightPoll) return leftPoll - rightPoll;
    }
    return (a.company_name || a.key).localeCompare(b.company_name || b.key) ||
      a.source.localeCompare(b.source) || a.key.localeCompare(b.key);
  });
}
