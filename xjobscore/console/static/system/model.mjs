/* Scheduler board model: pure functions over the server's per-vendor
   summaries (`/api/scheduler/providers`) and the board-group layout. Nothing
   here touches the DOM, so all of it runs under `node --test`. */

export const INTERVAL_PRESETS = [
  [300, "5m"],
  [900, "15m"],
  [3600, "1h"],
  [21600, "6h"],
  [86400, "24h"],
];

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

/* The board's words for scheduler states: short enough for a status column. */
export const STATE_LABEL = {
  backing_off: "err",
  stale: "stale",
  never_polled: "new",
  healthy: "ok",
  paused: "paused",
  unknown: "?",
};

/* The order a vendor's status lines appear in: problems, then held, then ok. */
export const STATE_ORDER = ["backing_off", "stale", "never_polled", "paused", "healthy", "unknown"];

/* Which states each view pill keeps. `all` keeps everything. */
export function viewMatches(state, view) {
  if (view === "issues") return ATTENTION_STATES.has(state);
  if (view === "failing") return state === "backing_off";
  if (view === "paused") return state === "paused";
  return true;
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

/* "15m", "15m–6h", or "—": a status line or vendor spanning several intervals. */
export function intervalRange(min, max, format) {
  if (min == null) return "—";
  return min === max || max == null ? format(min) : `${format(min)}–${format(max)}`;
}

function earliest(a, b) {
  if (a == null) return b;
  if (b == null) return a;
  return a < b ? a : b;
}

function latest(a, b) {
  if (a == null) return b;
  if (b == null) return a;
  return a > b ? a : b;
}

function addNullable(a, b) {
  return b == null ? a : (a ?? 0) + b;
}

/* A vendor row's columns, rolled up from its status lines. `worst` is the
   state its status column shows. Sums stay null when no line carries them. */
export function providerRollup(provider) {
  const buckets = provider.buckets ?? [];
  let total = 0;
  let intervalMin = null;
  let intervalMax = null;
  let dueMin = null;
  let lastMax = null;
  let jobs = null;
  let errors = null;
  for (const bucket of buckets) {
    total += bucket.count;
    intervalMin = earliest(intervalMin, bucket.interval_min);
    intervalMax = latest(intervalMax, bucket.interval_max);
    dueMin = earliest(dueMin, bucket.due_min);
    lastMax = latest(lastMax, bucket.last_max);
    jobs = addNullable(jobs, bucket.jobs);
    errors = addNullable(errors, bucket.errors);
  }
  const worst = [...buckets].sort(
    (a, b) => (STATUS_PRIORITY[a.state] ?? 99) - (STATUS_PRIORITY[b.state] ?? 99)
  )[0]?.state ?? null;
  return { total, worst, intervalMin, intervalMax, dueMin, lastMax, jobs, errors };
}

/* A vendor's status lines in board order, narrowed to the active view. */
export function visibleBuckets(provider, view) {
  return STATE_ORDER
    .map((state) => (provider.buckets ?? []).find((bucket) => bucket.state === state))
    .filter((bucket) => bucket && viewMatches(bucket.state, view));
}

const SORT_VALUE = {
  status: (rollup) => STATUS_PRIORITY[rollup.worst] ?? 99,
  name: (_rollup, provider) => provider.source,
  interval: (rollup) => rollup.intervalMin ?? Infinity,
  due: (rollup) => (rollup.dueMin ? Date.parse(rollup.dueMin) : Infinity),
  last_poll: (rollup) => (rollup.lastMax ? Date.parse(rollup.lastMax) : -Infinity),
  jobs: (rollup) => rollup.jobs ?? -Infinity,
  errors: (rollup) => rollup.errors ?? -Infinity,
};

/* Vendors sorted by one column; ties fall back to name so the order is stable. */
export function sortProviders(providers, sort = { key: "status", direction: "asc" }) {
  const value = SORT_VALUE[sort.key] ?? SORT_VALUE.status;
  const direction = sort.direction === "desc" ? -1 : 1;
  return providers
    .map((provider) => ({ provider, rollup: providerRollup(provider) }))
    .sort((a, b) => {
      const left = value(a.rollup, a.provider);
      const right = value(b.rollup, b.provider);
      if (left < right) return -direction;
      if (left > right) return direction;
      return a.provider.source.localeCompare(b.provider.source);
    })
    .map(({ provider }) => provider);
}

/* Page links for a pager: first, last, and a window around the current page,
   with null standing for a gap ("…"). */
export function pageWindow(page, pages, around = 1) {
  if (pages <= 1) return [1];
  const keep = new Set([1, pages]);
  for (let n = page - around; n <= page + around; n += 1) if (n >= 1 && n <= pages) keep.add(n);
  const sorted = [...keep].sort((a, b) => a - b);
  const out = [];
  for (const [index, n] of sorted.entries()) {
    if (index && n - sorted[index - 1] > 1) out.push(null);
    out.push(n);
  }
  return out;
}

/* ---------- board sections (operator-named provider groups) ----------

   The server stores a layout: groups in board order, each with the providers
   ("sources") filed in it. Anything not filed is Ungrouped, which is not a
   stored group -- it is always last and cannot be renamed, so a provider that
   appears tomorrow lands somewhere visible on its own. Layout edits below
   return a new layout and never mutate, so the previous one stays intact for
   undo. */

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

/* Vendors bucketed into the layout's sections, Ungrouped last, in the order
   given. Sections with no vendor to show are dropped. */
export function sectionProviders(providers, layout) {
  const sections = layout.map((group) => ({
    id: sectionKey(group.id),
    name: group.name,
    ungrouped: false,
    providers: [],
  }));
  const ungrouped = { id: UNGROUPED, name: UNGROUPED_NAME, ungrouped: true, providers: [] };
  const byId = new Map(sections.map((section) => [section.id, section]));
  for (const provider of providers) {
    (byId.get(sectionOf(layout, provider.source)) ?? ungrouped).providers.push(provider);
  }
  return [...sections, ungrouped].filter((section) => section.providers.length);
}

/* A section heading's state counts, summed over its vendors, worst first. */
export function sectionCounts(section) {
  const counts = {};
  for (const provider of section.providers) {
    for (const [state, count] of Object.entries(provider.counts ?? {})) {
      counts[state] = (counts[state] ?? 0) + count;
    }
  }
  return STATE_ORDER.filter((state) => counts[state]).map((state) => [state, counts[state]]);
}

function withoutSource(layout, source) {
  return layout.map((group) => ({
    ...group,
    sources: group.sources.filter((item) => item !== source),
  }));
}

/* Groups only exist while something is filed in them: emptying one drops it. */
export function dropEmptySections(layout) {
  return layout.filter((group) => group.sources.length);
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

export function uniqueSectionName(layout, base = "New group") {
  const taken = new Set(layout.map((group) => group.name.toLowerCase()));
  let name = base;
  for (let n = 2; taken.has(name.toLowerCase()); n += 1) name = `${base} ${n}`;
  return name;
}
