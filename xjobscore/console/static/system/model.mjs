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

// No `disabled`: pause and disable were one intent over two columns, and are
// now one state over `feed.polling`.
export const STATUS_PRIORITY = {
  backing_off: 0,
  stale: 1,
  never_polled: 2,
  healthy: 3,
  paused: 4,
  unknown: 5,
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
   overdue and near-due feeds are flagged imminent so the UI can highlight them.
   `held` is the negation of feed.policy.enabled -- a feed nobody is polling has
   no next due date to show. */
export function dueInfo(dueAt, held, nowMs) {
  if (held) return { sortKey: Infinity, text: "—", imminent: false };
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
  if (filters.inSection && !filters.inSection(feed.source)) return false;
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
    return dueInfo(feed.policy?.due_at, feed.policy?.enabled === false, nowMs).sortKey;
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

/* Provider grouping. A group's rows are the feeds that pass the current
   filters, sorted by the active column; the group order is fixed by concern —
   most failing feeds first, then size — so a column sort never moves the group
   an operator is looking at. allRows retains the complete provider for its
   pause/resume control. Groups with no matching rows are dropped. */
export function groupFeeds(feeds, filters, sort = { key: "due", direction: "asc" }, nowMs = 0) {
  const groups = new Map();
  for (const feed of feeds) {
    let group = groups.get(feed.source);
    if (!group) {
      group = { source: feed.source, total: 0, rows: [], allRows: [] };
      groups.set(feed.source, group);
    }
    group.total += 1;
    group.allRows.push(feed);
    if (matchesFeed(feed, filters)) group.rows.push(feed);
  }
  const result = [];
  for (const group of groups.values()) {
    if (!group.rows.length) continue;
    group.rows = sortFeeds(group.rows, sort, nowMs);
    group.rollup = groupRollup(group.rows, nowMs);
    result.push(group);
  }
  return result.sort(
    (a, b) =>
      b.rollup.failing - a.rollup.failing ||
      b.rows.length - a.rows.length ||
      a.source.localeCompare(b.source)
  );
}

/* Header-row aggregates for one group. Sums stay unknown (null) when no row
   carries the observation, matching the per-feed "—" rather than inventing 0. */
export function groupRollup(rows, nowMs = 0) {
  const counts = {};
  const intervals = new Set();
  let failing = 0;
  let jobs = null;
  let errors = null;
  let soonestKey = Infinity;
  let dueAt = null;
  let latestPoll = -Infinity;
  for (const feed of rows) {
    const state = feed.sched.status.state;
    counts[state] = (counts[state] ?? 0) + 1;
    if (state === "backing_off") failing += 1;
    const today = jobsToday(feed);
    if (today != null) jobs = (jobs ?? 0) + today;
    const errs = feed.telemetry?.error_count_7d;
    if (errs != null) errors = (errors ?? 0) + errs;
    const due = dueInfo(feed.policy?.due_at, feed.policy?.enabled === false, nowMs);
    if (due.sortKey < soonestKey) {
      soonestKey = due.sortKey;
      dueAt = feed.policy.due_at;
    }
    const poll = Date.parse(feed.policy?.last_poll_at ?? "");
    if (!Number.isNaN(poll) && poll > latestPoll) latestPoll = poll;
    if (feed.policy?.interval_s != null) intervals.add(feed.policy.interval_s);
  }
  const states = Object.keys(counts).sort(
    (a, b) => (STATUS_PRIORITY[a] ?? 99) - (STATUS_PRIORITY[b] ?? 99)
  );
  return {
    worst: states[0] ?? "unknown",
    counts: states.map((state) => [state, counts[state]]),
    failing,
    jobs,
    errors,
    dueAt,
    lastPollAt: latestPoll === -Infinity ? null : new Date(latestPoll).toISOString(),
    intervals: [...intervals].sort((a, b) => a - b),
  };
}

/* Collapsed groups are the default; a narrowing filter that leaves a small
   result set opens every matching group so the rows are visible at once. */
export const AUTO_EXPAND_ROWS = 150;

export function autoExpand(groups, filters) {
  if (filters.view === "all" && !filters.search) return false;
  const rows = groups.reduce((sum, group) => sum + group.rows.length, 0);
  return rows <= AUTO_EXPAND_ROWS;
}

/* ---------- board sections (operator-named provider groups) ----------

   The server stores a layout: groups in board order, each with the providers
   ("sources") filed in it. Anything not filed is Ungrouped, which is not a
   stored group -- it is always last and cannot be renamed, moved or deleted,
   so a provider that appears tomorrow lands somewhere visible on its own.
   Layout edits below return a new layout and never mutate, so the previous
   one stays intact for undo. */

export const UNGROUPED = "ungrouped";
export const UNGROUPED_NAME = "Ungrouped";

export const sectionKey = (id) => (id == null ? null : String(id));

/* `group:finance` -- or group:"long tail" -- narrows the board to sections
   whose name contains the needle; the rest of the box is the usual search. */
export function parseSearch(raw = "") {
  let group = null;
  const text = raw
    .replace(/group:(?:"([^"]*)"|(\S*))/i, (_, quoted, word) => {
      group = (quoted ?? word).toLowerCase();
      return "";
    })
    .trim();
  return { text, group };
}

export function sectionOf(layout, source) {
  const owner = layout.find((group) => group.sources.includes(source));
  return owner ? sectionKey(owner.id) : UNGROUPED;
}

/* A source predicate for a `group:` needle, or null when there is none. */
export function sectionMatcher(layout, needle) {
  if (needle == null) return null;
  const named = new Set(
    layout
      .filter((group) => group.name.toLowerCase().includes(needle))
      .flatMap((group) => group.sources)
  );
  const filed = new Set(layout.flatMap((group) => group.sources));
  const ungrouped = UNGROUPED_NAME.toLowerCase().includes(needle);
  return (source) => named.has(source) || (ungrouped && !filed.has(source));
}

/* Buckets provider groups (from groupFeeds, already in concern order) into
   the layout's sections, Ungrouped last. Empty sections are kept; the caller
   decides whether an empty one is worth a heading. */
export function sectionGroups(providerGroups, layout) {
  const sections = layout.map((group) => ({
    id: sectionKey(group.id),
    name: group.name,
    ungrouped: false,
    groups: [],
  }));
  const ungrouped = { id: UNGROUPED, name: UNGROUPED_NAME, ungrouped: true, groups: [] };
  const byId = new Map(sections.map((section) => [section.id, section]));
  for (const group of providerGroups) {
    (byId.get(sectionOf(layout, group.source)) ?? ungrouped).groups.push(group);
  }
  return [...sections, ungrouped];
}

/* Section heading aggregates, summed from its providers' rollups. Sums stay
   null when no provider carries the observation, matching groupRollup. */
export function sectionRollup(section) {
  const counts = {};
  let jobs = null;
  let errors = null;
  let feeds = 0;
  for (const group of section.groups) {
    feeds += group.rows.length;
    for (const [state, count] of group.rollup.counts) counts[state] = (counts[state] ?? 0) + count;
    if (group.rollup.jobs != null) jobs = (jobs ?? 0) + group.rollup.jobs;
    if (group.rollup.errors != null) errors = (errors ?? 0) + group.rollup.errors;
  }
  const states = Object.keys(counts).sort(
    (a, b) => (STATUS_PRIORITY[a] ?? 99) - (STATUS_PRIORITY[b] ?? 99)
  );
  return {
    worst: states[0] ?? null,
    counts: states.map((state) => [state, counts[state]]),
    providers: section.groups.length,
    feeds,
    jobs,
    errors,
  };
}

function withoutSource(layout, source) {
  return layout.map((group) => ({
    ...group,
    sources: group.sources.filter((item) => item !== source),
  }));
}

export function moveSource(layout, source, target) {
  const next = withoutSource(layout, source);
  if (target === UNGROUPED) return next;
  return next.map((group) =>
    sectionKey(group.id) === target ? { ...group, sources: [...group.sources, source] } : group
  );
}

/* A new group is appended with a null id; the server assigns one. */
export function addSection(layout, name, sources = []) {
  const cleared = sources.reduce(withoutSource, layout);
  return [...cleared, { id: null, name, sources: [...sources] }];
}

export function renameSection(layout, id, name) {
  return layout.map((group) => (sectionKey(group.id) === id ? { ...group, name } : group));
}

export function removeSection(layout, id) {
  return layout.filter((group) => sectionKey(group.id) !== id);
}

/* Moves a group to `index` in the board order (clamped). */
export function moveSection(layout, id, index) {
  const from = layout.findIndex((group) => sectionKey(group.id) === id);
  if (from < 0) return layout;
  const next = [...layout];
  const [group] = next.splice(from, 1);
  next.splice(Math.max(0, Math.min(index, next.length)), 0, group);
  return next;
}

export function uniqueSectionName(layout, base = "New group") {
  const taken = new Set(layout.map((group) => group.name.toLowerCase()));
  let name = base;
  for (let n = 2; taken.has(name.toLowerCase()); n += 1) name = `${base} ${n}`;
  return name;
}
