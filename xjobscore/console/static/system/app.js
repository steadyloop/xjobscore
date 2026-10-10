import { loadReconcile } from "/static/system/reconcile.js";
/* System page — scheduler board (fleet view) plus the live panels in live.js.
 *
 * Cadence is entirely operator-owned: this page reads and writes interval_s,
 * but never estimates or changes it automatically. Poll telemetry remains raw
 * production evidence, and absent observations remain unknown rather than zero.
 */

import { api, isDegraded } from "/static/shared/api.js";
import {
  el,
  formatCount,
  formatInterval,
  relativeTime,
  statusChip,
} from "/static/shared/format.js";
import { dropFeed, muteFeed, setProviderPaused } from "/static/shared/feed-policy.js";
import { renderNav } from "/static/shared/nav.js";
import { sparkBars } from "/static/shared/spark.js";
import {
  ATTENTION_STATES,
  INTERVAL_PRESETS,
  UNGROUPED,
  UNGROUPED_NAME,
  addSection,
  autoExpand,
  dueInfo,
  formatRequestRate,
  groupFeeds,
  intervalError,
  intervalInput,
  intervalSeconds,
  jobsToday,
  matchesFeed,
  moveSection,
  moveSource,
  parseSearch,
  removeSection,
  renameSection,
  sectionGroups,
  sectionKey,
  sectionMatcher,
  sectionOf,
  sectionRollup,
  sortFeeds,
  summaryCounts,
  uniqueSectionName,
} from "/static/system/model.mjs";
import { clockTime, paintWorkers, setFeeds, startLive } from "/static/system/live.js";
import { setPollActivity } from "/static/system/activity.js";

renderNav("System");

const RENDER_CHUNK = 200;
const SEARCH_DEBOUNCE_MS = 180;
const OVERVIEW_REFRESH_MS = 30_000;
const FEEDS_REFRESH_MS = 60_000;
const LOG_REFRESH_MS = 5000;
const COUNTDOWN_TICK_MS = 15_000;
const BULK_FETCH_CONCURRENCY = 8;
const EXPANDED_STORAGE_KEY = "scheduler-expanded-providers";
const FOLDED_STORAGE_KEY = "scheduler-folded-groups";

const state = {
  feeds: [],
  overview: null,
  connected: false,
  telemetryAvailable: false,
  overviewError: null,
  feedsError: null,
  lastFeedUpdate: null,
  selectedRef: null,
  selectedTrigger: null,
  detailEvents: null,
  detailToken: 0,
  panelMessage: null,
  intervalDraft: null,
  search: "",
  view: "all",
  expanded: loadExpanded(),
  sort: { key: "due", direction: "asc" },
  bulk: null,
  bulkScope: null,
  pausingProviders: new Set(),
  logEvents: [],
  logAvailable: false,
  // Board groups: the server's layout, plus per-browser fold state.
  layout: [],
  groupsAvailable: false,
  groupsError: null,
  savingGroups: false,
  folded: loadFolded(),
  groupsPanelOpen: false,
  renaming: null,
  moveMenu: null,
};

const alertEl = document.getElementById("system-alert");
// Run state is shown by the spinner beside "Activity" (animated in live.js).
const activitySpinnerEl = document.getElementById("activity-spinner");
const runToggleEl = document.getElementById("run-toggle");
const schedulerHeadEl = document.getElementById("scheduler-head");
const clockEl = document.getElementById("local-clock");
const summaryEl = document.getElementById("summary-line");
const emptyEl = document.getElementById("sched-empty");
const countEl = document.getElementById("row-count");
const panelEl = document.getElementById("detail-panel");
const fetchMatchingEl = document.getElementById("fetch-matching");
const bulkDialogEl = document.getElementById("bulk-dialog");
const removeDialogEl = document.getElementById("remove-dialog");
const toastEl = document.getElementById("toast");
const groupsPanelEl = document.getElementById("groups-panel");
const moveMenuEl = document.getElementById("move-menu");
const editGroupsEl = document.getElementById("edit-groups");
const tableEl = document.getElementById("sched-table");
let renderToken = 0;
const tableWrapEl = document.querySelector(".ops-table-wrap");
let searchTimer = null;
let toastTimer = null;

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172_800) return `${(seconds / 3600).toFixed(1).replace(/\.0$/, "")} h`;
  return `${(seconds / 86_400).toFixed(seconds < 864_000 ? 1 : 0).replace(/\.0$/, "")} d`;
}

function exactTime(value) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const STATUS_TEXT = {
  healthy: "ok",
  backing_off: "err",
  stale: "stale",
  never_polled: "new",
  paused: "paused",
  unknown: "?",
};

// Pause and disable are one column now: `held` is simply polling = false.
function isHeld(feed) {
  return feed.policy?.enabled === false;
}

function statusMark(status) {
  return el(
    "span",
    { class: "ops-st", "data-state": status.state, title: status.detail },
    STATUS_TEXT[status.state] ?? status.state
  );
}

/* "26m", "5h", "3d" — the terse age used in the fleet table. */
function terseAgo(iso) {
  if (!iso) return "never";
  const seconds = (Date.now() - Date.parse(iso)) / 1000;
  if (Number.isNaN(seconds)) return iso;
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172_800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

function formatIvl(seconds) {
  return formatInterval(seconds).replace(/\s+/g, "");
}

/* `action` adds one inline button, e.g. { label: "undo", run }. */
function showToast(message, tone = "ok", action = null) {
  clearTimeout(toastTimer);
  toastEl.replaceChildren(
    message,
    ...(action
      ? [
          el("span", { class: "wk-sep", "aria-hidden": "true" }, "·"),
          el(
            "button",
            {
              class: "ops-linkish",
              type: "button",
              onclick: () => {
                toastEl.hidden = true;
                action.run();
              },
            },
            action.label
          ),
        ]
      : [])
  );
  toastEl.dataset.tone = tone;
  toastEl.hidden = false;
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, action ? 6000 : 4200);
}

const CLOCK_FORMAT = new Intl.DateTimeFormat(undefined, {
  hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true, timeZoneName: "short",
});

function renderClock() {
  clockEl.textContent = CLOCK_FORMAT.format(new Date());
}

function renderHeader() {
  const control = state.overview?.control;
  const runner = state.overview?.runner;
  const running = control?.running ?? false;
  const alive = Boolean(runner?.alive);

  if (!state.overview && !state.overviewError) {
    activitySpinnerEl.dataset.state = "loading";
    activitySpinnerEl.title = "Loading…";
  } else {
    const label = !running ? "Paused" : alive ? "Running" : "Runner offline";
    activitySpinnerEl.dataset.state = !running ? "paused" : alive ? "running" : "offline";
    const detail = runner?.last_cycle_at
      ? `Last cycle ${relativeTime(runner.last_cycle_at)}${runner.last_polled != null ? ` · ${formatCount(runner.last_polled)} feeds polled` : ""}`
      : running ? "No completed cycles yet" : "Production polling is stopped";
    activitySpinnerEl.title = `${label} · ${detail}`;
  }
  activitySpinnerEl.setAttribute("aria-label", activitySpinnerEl.title);

  // The Scheduler spinner (live.js) turns only while polling is on and a
  // runner is actually listening -- the same condition as "running" above.
  schedulerHeadEl.dataset.live = running && alive ? "on" : "off";

  runToggleEl.disabled = !state.connected || !state.overview;
  runToggleEl.textContent = running ? "Pause" : "Resume";
  runToggleEl.classList.toggle("primary", !running && state.connected);
  runToggleEl.title = running ? "Pause all scheduled polling" : "Resume scheduled polling";
  fetchMatchingEl.disabled = !state.connected;
}

function renderAlert() {
  let title = null;
  let message = null;
  let level = "warning";

  if (state.overviewError || state.feedsError) {
    title = "Console data is unavailable";
    message = state.overviewError || state.feedsError;
    level = "error";
  } else if (!state.connected) {
    title = "PostgreSQL is unavailable";
    message = "Production state cannot be read. Scheduler controls are disabled until the connection returns.";
    level = "error";
  } else if (state.overview?.control?.running && !state.overview?.runner?.alive) {
    title = "No scheduler runner is listening";
    message = "The run switch is on, but this Console process is not supervising production polling.";
  } else if (!state.telemetryAvailable) {
    title = "Polling telemetry is not available yet";
    message = "Activity values remain unknown until poll observations are available.";
  }

  alertEl.hidden = !title;
  if (!title) return;
  alertEl.dataset.level = level;
  alertEl.replaceChildren(
    el("div", {}, el("strong", {}, title), el("span", {}, message))
  );
}

function renderSummaryLine() {
  if (!state.overview) {
    summaryEl.textContent = state.overviewError
      ? "Scheduler state unavailable."
      : "Loading scheduler state…";
    return;
  }
  const counts = summaryCounts(state.overview.counts_by_status);
  const parts = [
    el("span", { class: "ops-sum-ok" }, `${formatCount(counts.ok)} ok`),
    el("span", { class: counts.erroring ? "ops-sum-bad" : null }, `${formatCount(counts.erroring)} erroring`),
    el("span", { class: "sys-quiet" }, `${formatCount(counts.paused)} paused`),
  ];
  summaryEl.replaceChildren(
    ...parts.flatMap((part, index) => [
      index ? el("span", { class: "ops-sum-sep", "aria-hidden": "true" }, "·") : null,
      typeof part === "string" ? el("span", {}, part) : part,
    ]).filter(Boolean)
  );
}

/* One grid row holds the toggle plus this many chips. */
/* Provider groups start collapsed; which ones an operator opened survives a
   reload. Storage failures (private mode, quota) only cost that persistence. */
function loadExpanded() {
  try {
    const stored = JSON.parse(localStorage.getItem(EXPANDED_STORAGE_KEY) ?? "[]");
    return new Set(Array.isArray(stored) ? stored : []);
  } catch {
    return new Set();
  }
}

function persistExpanded() {
  try {
    localStorage.setItem(EXPANDED_STORAGE_KEY, JSON.stringify([...state.expanded]));
  } catch {
    /* expansion just will not survive a reload */
  }
}

/* Board sections start unfolded; only the ones folded here are remembered. */
function loadFolded() {
  try {
    const stored = JSON.parse(localStorage.getItem(FOLDED_STORAGE_KEY) ?? "[]");
    return new Set(Array.isArray(stored) ? stored : []);
  } catch {
    return new Set();
  }
}

function persistFolded() {
  try {
    localStorage.setItem(FOLDED_STORAGE_KEY, JSON.stringify([...state.folded]));
  } catch {
    /* folds just will not survive a reload */
  }
}

function toggleSection(id) {
  if (state.folded.has(id)) state.folded.delete(id);
  else state.folded.add(id);
  persistFolded();
  renderTable({ preserveScroll: true });
}

function toggleGroup(source) {
  if (state.expanded.has(source)) state.expanded.delete(source);
  else state.expanded.add(source);
  persistExpanded();
  renderTable({ preserveScroll: true });
}

function setAllExpanded(expanded) {
  state.expanded = new Set(expanded ? state.feeds.map((feed) => feed.source) : []);
  persistExpanded();
  if (expanded) {
    state.folded = new Set();
    persistFolded();
  }
  renderTable();
}

function renderViewCounts() {
  const counts = {
    all: state.feeds.length,
    issues: state.feeds.filter((feed) => ATTENTION_STATES.has(feed.sched.status.state)).length,
    failing: state.feeds.filter((feed) => feed.sched.status.state === "backing_off").length,
    paused: state.feeds.filter((feed) => feed.sched.status.state === "paused").length,
  };
  for (const button of document.querySelectorAll("[data-view]")) {
    const name = button.dataset.view;
    button.setAttribute("aria-pressed", String(state.view === name));
    button.querySelector("span").textContent = formatCount(counts[name]);
  }
}

function renderSystem() {
  renderHeader();
  renderAlert();
  renderSummaryLine();
  renderViewCounts();
}

async function loadOverview() {
  try {
    const [view] = await Promise.all([api("/api/scheduler/overview"), loadReconcile()]);
    state.overview = view;
    state.connected = Boolean(view.postgres?.connected);
    state.telemetryAvailable = Boolean(view.telemetry_available);
    state.overviewError = null;
  } catch (error) {
    state.overviewError = error.message;
    if (isDegraded(error)) state.connected = false;
    throw error;
  } finally {
    renderSystem();
  }
}

async function loadFeeds() {
  try {
    const view = await api("/api/scheduler/feeds");
    state.feeds = view.feeds ?? [];
    setFeeds(state.feeds);
    state.connected = Boolean(view.postgres?.connected);
    state.telemetryAvailable = Boolean(view.telemetry_available);
    state.feedsError = null;
    state.lastFeedUpdate = new Date();
    renderSummaryLine();
    renderViewCounts();
    renderTable({ preserveScroll: true });
  } catch (error) {
    state.feedsError = error.message;
    if (isDegraded(error)) state.connected = false;
    renderAlert();
    renderTable();
    throw error;
  }
}

// One cell, not a word plus a number: "ERR 404" states the failure twice, and
// at 86% `unchanged` the log's job is to stay quiet until something happens.
// A failed poll shows its status code, coloured -- the number IS the word.
// The fallback only appears when there is no code to show (a transport error,
// or any row written before poll_log.status_code existed), where the word is
// the only signal left.
// ok is spelled out -- "ok" is already the shortest word for it, so a glyph
// would not save space, only meaning. unchanged/partial share a checkmark
// (both mean the poll succeeded) and error gets the ballot X.
const EVENT_MARK = { ok: "ok", unchanged: "\u2713", partial: "\u2713", error: "\u2717" };
const EVENT_LABEL = { ok: "OK", unchanged: "Unchanged", partial: "Partial", error: "Error" };

function eventMark(status, statusCode) {
  return el(
    "span",
    { class: "ops-event-status" },
    el(
      "span",
      {
        class: "ops-event-mark",
        "data-status": status,
        title: EVENT_LABEL[status] ?? status,
      },
      EVENT_MARK[status] ?? "\u2013"
    ),
    statusCode ? el("span", { class: "ops-event-code" }, String(statusCode)) : null
  );
}

function renderLiveLog() {
  setPollActivity(state.logEvents, state.logAvailable);
}

async function loadLog() {
  try {
    const view = await api(`/api/scheduler/events?limit=50`);
    state.logAvailable = Boolean(view.telemetry_available);
    state.logEvents = view.events ?? [];
  } catch {
    state.logAvailable = false;
  }
  renderLiveLog();
}

function filters() {
  const { text, group } = parseSearch(state.search);
  return {
    search: text,
    view: state.view,
    inSection: sectionMatcher(state.layout, group),
  };
}

function matchingFeeds() {
  return state.feeds.filter((feed) => matchesFeed(feed, filters()));
}

function visibleFeeds() {
  return sortFeeds(matchingFeeds(), state.sort, Date.now());
}

function renderSortHeaders() {
  for (const th of document.querySelectorAll("#sched-table th[data-sort]")) {
    if (state.sort.key === th.dataset.sort) {
      th.setAttribute("aria-sort", state.sort.direction === "desc" ? "descending" : "ascending");
    } else {
      th.removeAttribute("aria-sort");
    }
  }
}

function dash(className = null) {
  return el("td", { class: className }, el("span", { class: "muted" }, "—"));
}

function rowActionCell(feed) {
  const blocked = fetchBlocked(feed);
  const canEdit = state.connected && feed.policy?.in_postgres;
  return el(
    "td",
    { class: "ops-row-actions" },
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: blocked ? "" : null,
        title: blocked ?? "Fetch this feed right now",
        onclick: (event) => fetchFeedNow(feed, event.currentTarget),
      },
      "fetch"
    ),
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: canEdit ? null : "",
        onclick: (event) => setPaused(feed, !isHeld(feed), event.currentTarget),
      },
      isHeld(feed) ? "resume" : "pause"
    )
  );
}

function feedRow(feed, nowMs) {
  const status = feed.sched.status;
  const name = feed.company_name || feed.key;
  const due = dueInfo(feed.policy?.due_at, isHeld(feed), nowMs);
  const jobs = jobsToday(feed);
  const errs = feed.telemetry?.error_count_7d ?? 0;
  return el(
    "tr",
    {
      class: "ops-feed",
      dataset: { feedRef: feed.feed_ref },
      "aria-selected": feed.feed_ref === state.selectedRef ? "true" : "false",
      onclick: (event) => {
        if (!event.target.closest("button, a, input, select")) {
          selectFeed(feed.feed_ref, event.currentTarget.querySelector(".ops-feed-button"));
        }
      },
    },
    el("td", { class: "ops-status-cell" }, statusMark(status)),
    el(
      "td",
      { class: "ops-source-cell" },
      el(
        "button",
        {
          class: "ops-feed-button",
          type: "button",
          onclick: (event) => selectFeed(feed.feed_ref, event.currentTarget),
        },
        name
      ),
      el("span", { class: "ops-feed-key" }, feed.key)
    ),
    el("td", { class: "sys-workers-cell", dataset: { workerFeed: `${feed.source}:${feed.key}` } }),
    state.connected && feed.policy?.in_postgres
      ? el("td", { class: "num" }, formatIvl(feed.policy.interval_s))
      : dash("num"),
    el(
      "td",
      {
        class: `num ops-next${due.imminent ? " ops-imminent" : ""}`,
        "data-due-at": due.sortKey !== Infinity ? feed.policy.due_at : null,
        title: exactTime(feed.policy?.due_at),
      },
      el("span", {}, due.text)
    ),
    state.connected
      ? el("td", { class: "num secondary", title: exactTime(feed.policy?.last_poll_at) }, terseAgo(feed.policy?.last_poll_at))
      : dash("num"),
    jobs != null
      ? el("td", { class: "num" }, formatCount(jobs))
      : dash("num"),
    el(
      "td",
      { class: "num ops-errors-cell" },
      errs ? el("span", { class: "ops-err" }, `x${formatCount(errs)}`) : null
    ),
    rowActionCell(feed)
  );
}

function groupId(source) {
  return `group-${source.replace(/[^\w-]/g, "_")}`;
}

function intervalsCell(intervals) {
  if (!state.connected || !intervals.length) return dash("num");
  const text = intervals.length === 1
    ? formatIvl(intervals[0])
    : `${formatIvl(intervals[0])}–${formatIvl(intervals.at(-1))}`;
  return el("td", { class: "num secondary" }, text);
}

/* Fetch follows the visible rows. Pause/resume covers the whole provider in
   one transaction, including feeds hidden by the current search or filter. */
function groupActionCell(group) {
  const editable = group.allRows.filter((feed) => feed.policy?.in_postgres);
  const allPaused = editable.length > 0 && editable.every(isHeld);
  const pausing = state.pausingProviders.has(group.source);
  const pollable = group.rows.filter((feed) => !isHeld(feed));
  return el(
    "td",
    { class: "ops-row-actions" },
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: state.connected && pollable.length ? null : "",
        title: state.connected
          ? `Fetch ${formatCount(pollable.length)} unpaused ${group.source} feeds through the production pipeline`
          : "PostgreSQL is unavailable.",
        onclick: () => openBulkDialog({ feeds: group.rows, label: group.source }),
      },
      "fetch"
    ),
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: state.connected && editable.length && !pausing ? null : "",
        title: state.connected
          ? `${allPaused ? "Resume" : "Pause"} all ${formatCount(editable.length)} ${group.source} feeds`
          : "PostgreSQL is unavailable.",
        onclick: (event) => setPausedMany(editable, !allPaused, group.source, event.currentTarget),
      },
      pausing ? "applying…" : allPaused ? "resume" : "pause"
    )
  );
}

/* One header row per provider: worst state, a "N err / N stale / N feeds"
   breakdown ordered by concern, and the same aggregate columns as a feed row. */
function groupRow(group, open, nowMs) {
  const { rollup } = group;
  const due = dueInfo(rollup.dueAt, null, nowMs);
  const filtered = group.rows.length !== group.total;
  const breakdown = rollup.counts.flatMap(([state, count]) => [
    el("span", {}, `${formatCount(count)} `),
    statusMark({ state, detail: null }),
    el("span", { class: "ops-group-sep", "aria-hidden": "true" }, "/"),
  ]);
  return el(
    "tr",
    {
      class: "ops-group",
      onclick: (event) => {
        if (!event.target.closest("button, a, input, select")) toggleGroup(group.source);
      },
    },
    el("td", { class: "ops-status-cell" }, statusMark({ state: rollup.worst, detail: null })),
    el(
      "td",
      { class: "ops-group-cell" },
      moveHandle(group.source),
      el(
        "button",
        {
          class: "ops-group-toggle",
          type: "button",
          "aria-expanded": String(open),
          "aria-controls": groupId(group.source),
          onclick: () => toggleGroup(group.source),
        },
        el("span", { class: "ops-chevron", "aria-hidden": "true" }, "▶"),
        el("span", { class: "ops-group-name" }, group.source),
        el(
          "span",
          { class: "ops-group-count" },
          ...breakdown,
          filtered
            ? [el("b", {}, formatCount(group.rows.length)), ` of ${formatCount(group.total)} feeds`]
            : [el("b", {}, formatCount(group.total)), " feeds"]
        )
      )
    ),
    el("td", { class: "sys-workers-cell", dataset: { workerSource: group.source } }),
    intervalsCell(rollup.intervals),
    el(
      "td",
      {
        class: `num ops-next${due.imminent ? " ops-imminent" : ""}`,
        "data-due-at": due.sortKey !== Infinity ? rollup.dueAt : null,
        title: exactTime(rollup.dueAt),
      },
      el("span", {}, due.text)
    ),
    state.connected
      ? el("td", { class: "num secondary", title: exactTime(rollup.lastPollAt) }, terseAgo(rollup.lastPollAt))
      : dash("num"),
    rollup.jobs != null ? el("td", { class: "num" }, formatCount(rollup.jobs)) : dash("num"),
    el(
      "td",
      { class: "num ops-errors-cell" },
      rollup.errors ? el("span", { class: "ops-err" }, `x${formatCount(rollup.errors)}`) : null
    ),
    groupActionCell(group)
  );
}

/* ---------- board sections ---------- */

function sectionName(id) {
  if (id === UNGROUPED) return UNGROUPED_NAME;
  return state.layout.find((group) => sectionKey(group.id) === id)?.name ?? UNGROUPED_NAME;
}

function groupsBlocked() {
  if (state.groupsAvailable) return null;
  return state.groupsError ? `Groups unavailable: ${state.groupsError}` : "Groups unavailable.";
}

/* The row handle: hidden until its row is hovered, it opens the move menu. */
function moveHandle(source) {
  const name = sectionName(sectionOf(state.layout, source));
  const blocked = groupsBlocked();
  return el(
    "button",
    {
      class: "ops-row-handle",
      type: "button",
      dataset: { moveSource: source },
      "aria-haspopup": "menu",
      "aria-expanded": String(state.moveMenu?.source === source),
      "aria-label": `Move ${source} to another group (now in ${name})`,
      title: blocked ?? `In ${name} · move to another group`,
      disabled: Boolean(blocked),
      onclick: (event) => {
        event.stopPropagation();
        if (state.moveMenu?.source === source) closeMoveMenu();
        else openMoveMenu(source, event.currentTarget);
      },
    },
    "⠿"
  );
}

/* A section heading: its name, a provider/feed breakdown, and the same
   aggregate columns as a provider row. Folded, it is one row that size. */
function sectionRow(section, open) {
  const rollup = sectionRollup(section);
  const breakdown = rollup.counts.flatMap(([state, count]) => [
    el("span", {}, `${formatCount(count)} `),
    statusMark({ state, detail: null }),
    el("span", { class: "ops-group-sep", "aria-hidden": "true" }, "/"),
  ]);
  const count = rollup.providers
    ? [
        el("b", {}, formatCount(rollup.providers)),
        rollup.providers === 1 ? " provider" : " providers",
        el("span", { class: "ops-group-sep", "aria-hidden": "true" }, "·"),
        ...breakdown,
        el("b", {}, formatCount(rollup.feeds)),
        " feeds",
      ]
    : ["empty · move providers here with ⠿"];
  return el(
    "tbody",
    {
      class: "ops-section-body",
      dataset: { open: String(open), ungrouped: String(section.ungrouped) },
    },
    el(
      "tr",
      { class: "ops-section", onclick: () => toggleSection(section.id) },
      el("td", { class: "ops-status-cell" }, rollup.worst ? statusMark({ state: rollup.worst, detail: null }) : null),
      el(
        "td",
        { class: "ops-group-cell" },
        el(
          "button",
          {
            class: "ops-section-toggle",
            type: "button",
            "aria-expanded": String(open),
            onclick: (event) => {
              event.stopPropagation();
              toggleSection(section.id);
            },
          },
          el("span", { class: "ops-chevron", "aria-hidden": "true" }, "▶"),
          el("span", { class: "ops-section-name" }, section.name),
          el("span", { class: "ops-group-count" }, ...count)
        )
      ),
      el("td", {
        class: "sys-workers-cell",
        dataset: { workerSources: section.groups.map((group) => group.source).join(" ") },
      }),
      el("td", {}),
      el("td", {}),
      el("td", {}),
      el("td", { class: "num ops-section-sum" }, rollup.jobs != null ? formatCount(rollup.jobs) : null),
      el(
        "td",
        { class: "num ops-errors-cell" },
        rollup.errors ? el("span", { class: "ops-err" }, `x${formatCount(rollup.errors)}`) : null
      ),
      el("td", {})
    )
  );
}

function syncSelectedRow() {
  for (const row of tableEl.querySelectorAll("tr[data-feed-ref]")) {
    row.setAttribute("aria-selected", String(row.dataset.feedRef === state.selectedRef));
  }
}

function renderTable({ preserveScroll = false } = {}) {
  const now = Date.now();
  const active = filters();
  const groups = groupFeeds(state.feeds, active, state.sort, now);
  const shown = groups.reduce((sum, group) => sum + group.rows.length, 0);
  const opened = autoExpand(groups, active);
  countEl.textContent =
    `${formatCount(shown)}/${formatCount(state.feeds.length)} shown · ${formatCount(groups.length)} providers`;
  renderSortHeaders();

  emptyEl.hidden = groups.length > 0;
  if (!groups.length) {
    emptyEl.textContent = state.feedsError
      ? `Could not load feeds: ${state.feedsError}`
      : !state.lastFeedUpdate
        ? "Loading feeds…"
        : state.feeds.length
          ? "No feeds match the current view. Clear the search and view filters to see the full fleet."
          : "No feeds are enabled. Enable a feed from the Feeds page to schedule it.";
  }

  const scrollState = preserveScroll ? {
    windowX: window.scrollX,
    windowY: window.scrollY,
    tableLeft: tableWrapEl.scrollLeft,
    height: tableWrapEl.offsetHeight,
  } : null;
  if (scrollState) tableWrapEl.style.minHeight = `${scrollState.height}px`;
  for (const body of [...tableEl.tBodies]) body.remove();

  /* Collapsed groups cost one row each; feed rows render lazily, in chunks,
     only for the groups that are open. */
  const pending = [];
  function appendGroup(group, ungrouped) {
    const open = opened || state.expanded.has(group.source);
    const body = el("tbody", {
      id: groupId(group.source),
      class: "ops-group-body",
      "data-expanded": String(open),
      "data-ungrouped": String(ungrouped),
    });
    body.append(groupRow(group, open, now));
    tableEl.append(body);
    if (open) pending.push({ body, rows: group.rows });
  }
  if (state.layout.length) {
    // Sections in the operator's order. A narrowing filter unfolds them all
    // and drops the empty ones; unfiltered, an empty named group still shows
    // so a group just created has somewhere visible to fill.
    const narrowed = active.view !== "all" || Boolean(state.search);
    for (const section of sectionGroups(groups, state.layout)) {
      if (!section.groups.length && (narrowed || section.ungrouped)) continue;
      const open = narrowed || !state.folded.has(section.id);
      tableEl.append(sectionRow(section, open));
      if (open) for (const group of section.groups) appendGroup(group, section.ungrouped);
    }
  } else {
    // No groups yet: the board is the plain provider list it always was.
    for (const group of groups) appendGroup(group, false);
  }
  // A refresh rebuilt the rows; keep an open move menu on its new handle.
  if (state.moveMenu) {
    const handle = tableEl.querySelector(
      `[data-move-source="${CSS.escape(state.moveMenu.source)}"]`
    );
    if (handle) state.moveMenu.anchor = handle;
    else closeMoveMenu();
  }
  tableEl.style.setProperty("--ops-thead-h", `${tableEl.tHead.offsetHeight}px`);
  paintWorkers();

  const token = ++renderToken;
  let groupIndex = 0;
  let rowIndex = 0;
  function step() {
    if (token !== renderToken) return;
    let budget = RENDER_CHUNK;
    while (groupIndex < pending.length && budget > 0) {
      const { body, rows } = pending[groupIndex];
      const slice = rows.slice(rowIndex, rowIndex + budget);
      body.append(...slice.map((feed) => feedRow(feed, now)));
      rowIndex += slice.length;
      budget -= slice.length;
      if (rowIndex >= rows.length) {
        groupIndex += 1;
        rowIndex = 0;
      }
    }
    if (groupIndex < pending.length) {
      requestAnimationFrame(step);
      return;
    }
    paintWorkers();
    if (scrollState) {
      tableWrapEl.style.minHeight = "";
      tableWrapEl.scrollLeft = scrollState.tableLeft;
      window.scrollTo(scrollState.windowX, scrollState.windowY);
    }
  }
  step();
}

function tickCountdowns() {
  const now = Date.now();
  for (const cell of tableEl.querySelectorAll("td[data-due-at]")) {
    const info = dueInfo(cell.dataset.dueAt, null, now);
    const label = cell.querySelector("span");
    if (label) label.textContent = info.text;
    cell.classList.toggle("ops-imminent", info.imminent);
  }
}

// telemetry.feed_daily_counts buckets by UTC day, so these labels format in UTC
// too -- a local-zone label would name the wrong day for viewers west of UTC.
function dayLabels(count) {
  const labels = [];
  for (let offset = count - 1; offset >= 0; offset -= 1) {
    const date = new Date();
    date.setUTCDate(date.getUTCDate() - offset);
    labels.push(date.toLocaleDateString(undefined, { timeZone: "UTC", month: "short", day: "numeric" }));
  }
  return labels;
}

function makeIntervalDraft(seconds) {
  const custom = !INTERVAL_PRESETS.some(([value]) => value === seconds);
  const input = intervalInput(seconds);
  return { seconds, custom, value: input.value, unit: input.unit, dirty: false };
}

function draftSeconds() {
  return state.intervalDraft?.custom
    ? intervalSeconds(state.intervalDraft.value, state.intervalDraft.unit)
    : state.intervalDraft?.seconds;
}

function updateIntervalForm(feed) {
  const seconds = draftSeconds();
  const error = intervalError(seconds);
  const current = feed.policy?.interval_s;
  state.intervalDraft.dirty = seconds !== current;
  const errorEl = document.getElementById("interval-error");
  const applyEl = document.getElementById("interval-apply");
  if (errorEl) errorEl.textContent = error ?? "";
  if (applyEl) applyEl.disabled = Boolean(error) || !state.intervalDraft.dirty || !state.connected;
}

function intervalEditor(feed) {
  const current = feed.policy?.interval_s;
  const draft = state.intervalDraft ?? makeIntervalDraft(current);
  state.intervalDraft = draft;
  const seconds = draftSeconds();
  const custom = draft.custom;

  const presetBar = el(
    "div",
    { class: "ops-presets", role: "group", "aria-label": "Polling interval" },
    ...INTERVAL_PRESETS.map(([value, label]) =>
      el(
        "button",
        {
          type: "button",
          "aria-pressed": !custom && seconds === value ? "true" : "false",
          disabled: state.connected && feed.policy?.in_postgres ? null : "",
          onclick: () => {
            state.intervalDraft = { ...makeIntervalDraft(value), custom: false, dirty: value !== current };
            renderDetailPanel(feed, state.detailEvents);
          },
        },
        label
      )
    ),
    el(
      "button",
      {
        type: "button",
        "aria-pressed": custom ? "true" : "false",
        disabled: state.connected && feed.policy?.in_postgres ? null : "",
        onclick: () => {
          const input = intervalInput(seconds || current);
          state.intervalDraft = { ...draft, custom: true, value: input.value, unit: input.unit };
          renderDetailPanel(feed, state.detailEvents);
          document.getElementById("custom-interval-value")?.focus();
        },
      },
      "Custom"
    )
  );

  const customFields = custom
    ? el(
        "div",
        { class: "ops-custom-interval" },
        el("input", {
          id: "custom-interval-value",
          type: "number",
          min: "1",
          step: "1",
          value: String(draft.value),
          "aria-label": "Custom interval value",
          oninput: (event) => {
            state.intervalDraft.value = event.target.value;
            updateIntervalForm(feed);
          },
        }),
        el(
          "select",
          {
            id: "custom-interval-unit",
            "aria-label": "Custom interval unit",
            onchange: (event) => {
              state.intervalDraft.unit = event.target.value;
              updateIntervalForm(feed);
            },
          },
          ["seconds", "minutes", "hours", "days"].map((unit) =>
            el("option", { value: unit, selected: draft.unit === unit ? "" : null }, unit)
          )
        )
      )
    : null;

  return el(
    "section",
    { class: "ops-panel-section" },
    el("h3", {}, "Interval"),
    el(
      "div",
      { class: "ops-interval-summary" },
      el("strong", {}, formatInterval(current)),
      el("span", {}, `${formatRequestRate(feed.sched.requests_per_day)} requests/day configured`)
    ),
    presetBar,
    customFields,
    el(
      "div",
      { class: "ops-interval-footer" },
      el("span", { class: "ops-field-error", id: "interval-error", "aria-live": "polite" }, intervalError(seconds) ?? ""),
      el(
        "button",
        {
          class: "btn small primary",
          id: "interval-apply",
          type: "button",
          disabled: intervalError(seconds) || seconds === current || !state.connected ? "" : null,
          onclick: (event) => applyInterval(feed, seconds, event.currentTarget),
        },
        "Apply interval"
      )
    ),
    el("p", { class: "ops-help" }, "Changes future cadence only. Use Fetch when this feed should run immediately.")
  );
}

function detailStat(label, value, title = null) {
  return el(
    "div",
    { class: "ops-detail-stat", title },
    el("dt", {}, label),
    el("dd", {}, value)
  );
}

/* Fetch bypasses the queue and the runner, so there are no
   scheduler-running/runner-alive conditions to check -- which is exactly why it
   replaced Poll now rather than sitting beside it. */
function fetchBlocked(feed) {
  if (!state.connected) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres) return "Import this feed before fetching it.";
  if (isHeld(feed)) return "Resume this feed before fetching it now.";
  if (feed.policy?.hold_until && new Date(feed.policy.hold_until) > new Date()) {
    return "A poll of this feed is already running.";
  }
  return null;
}

/* Mute and Drop both delete the feed row; the only difference is whether the
   discovery token is muted so the heartbeat cannot promote it back. They open
   the same dialog, preset to their own answer, so the choice is still visible
   -- and reversible -- at the moment of deletion rather than only in the label
   of the button already clicked. Panel-only on purpose: irreversible, so it
   stays behind an opened feed rather than sitting in every row of the board. */
function removeBlocked(feed) {
  if (!state.connected) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres || !feed.policy?.feed_id) {
    return "Not in the feed table — nothing to remove.";
  }
  return null;
}

/* Muting writes token.muted, and only ATS feeds have a discovery token. A
   Direct or Board feed can be dropped, just never kept from coming back. */
function canMute(feed) {
  return feed.category === "ATS" && !removeBlocked(feed);
}

function muteBlocked(feed) {
  return removeBlocked(feed) ?? (canMute(feed) ? null : "No discovery token to mute.");
}

async function runRemoval(feed, { rediscover }) {
  const label = feed.company_name || feed.key;
  try {
    // Muting is the atomic token-side mutation: it clears the feed row *and*
    // sets token.muted in one statement, so there is no drop-then-mute race.
    await (rediscover ? dropFeed(feed) : muteFeed(feed));
    if (state.selectedRef === feed.feed_ref) closePanel();
    await Promise.all([loadOverview(), loadFeeds()]);
    showToast(
      rediscover
        ? `${label} dropped; it can be rediscovered.`
        : `${label} muted and dropped.`
    );
  } catch (error) {
    reportFeedMessage(feed, `Could not remove this feed: ${error.message}`, "error");
  }
}

/* Rediscovery on is what Drop means: the heartbeat re-probes the token once it
   reads dead and promotes the feed back, so a dropped feed can return hours
   later. Mute is the one that stays gone. Pause covers the "just stop it for
   now" case, and is the only reversible member of the three. */
function openRemoveDialog(feed, { rediscover = false } = {}) {
  const label = feed.company_name || feed.key;
  const mutable = canMute(feed);
  const checkbox = el("input", { type: "checkbox", id: "remove-rediscover" });
  checkbox.checked = rediscover || !mutable;
  checkbox.disabled = !mutable;

  // The checkbox is the same switch as the two buttons that open this dialog,
  // so heading and confirm both track it rather than the button clicked.
  const verb = () => (checkbox.checked ? "Drop" : "Mute");
  const heading = el("h2", { id: "remove-title" }, `${verb()} ${label}?`);
  const confirmBtn = el("button", { class: "btn danger", type: "button" }, verb());
  checkbox.addEventListener("change", () => {
    heading.textContent = `${verb()} ${label}?`;
    confirmBtn.textContent = verb();
  });
  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Working…";
    removeDialogEl.close();
    await runRemoval(feed, { rediscover: checkbox.checked });
  });

  removeDialogEl.replaceChildren(
    el(
      "header",
      { class: "ops-dialog-header" },
      heading
    ),
    el(
      "div",
      { class: "ops-dialog-body" },
      el("p", {}, `${feed.source}:${feed.key} leaves the fleet. Its poll history goes with it; job postings already harvested are kept.`),
      el(
        "label",
        { class: "ops-dialog-check" },
        checkbox,
        el(
          "span",
          {},
          "Let this board be rediscovered automatically",
          mutable
            ? null
            : el("span", { class: "muted" }, " — no discovery token to mute")
        )
      )
    ),
    el(
      "div",
      { class: "ops-dialog-footer" },
      el("button", { class: "btn", type: "button", onclick: () => removeDialogEl.close() }, "Cancel"),
      confirmBtn
    )
  );
  removeDialogEl.showModal();
}

/* Feed-level outcome messaging: inline in the panel when the feed is the one
   on display, a toast otherwise (row actions can target any feed). */
function reportFeedMessage(feed, message, tone = "ok") {
  if (state.selectedRef === feed.feed_ref) {
    state.panelMessage = message;
    renderDetailPanel(feed, state.detailEvents);
  } else {
    showToast(message, tone);
  }
}

function renderDetailPanel(feed, events) {
  if (!feed || feed.feed_ref !== state.selectedRef) return;
  const status = feed.sched.status;
  const blocked = fetchBlocked(feed);
  const noRemove = removeBlocked(feed);
  const noMute = muteBlocked(feed);
  const spark = feed.spark ?? [];
  const labels = dayLabels(spark.length);

  const lastResult = feed.telemetry?.last_status
    ? statusChip(feed.telemetry.last_status)
    : el("span", { class: "muted" }, "—");

  panelEl.replaceChildren(
    el(
      "header",
      { class: "ops-panel-header" },
      el(
        "div",
        { class: "ops-panel-title" },
        el("div", { class: "ops-panel-title-row" }, statusMark(status), el("h2", { id: "panel-title" }, feed.company_name || feed.key)),
        el("p", {}, `${feed.source}:${feed.key}`)
      ),
      el("button", { class: "ops-close", type: "button", "aria-label": "Close feed details", onclick: closePanel }, "×")
    ),
    el(
      "div",
      { class: "ops-panel-actions" },
      el(
        "button",
        {
          class: "btn primary",
          type: "button",
          disabled: blocked ? "" : null,
          title: blocked ?? "Fetch this feed right now",
          onclick: (event) => fetchFeedNow(feed, event.currentTarget),
        },
        "Fetch"
      ),
      el(
        "button",
        {
          class: "btn",
          type: "button",
          disabled: state.connected && feed.policy?.in_postgres ? null : "",
          onclick: (event) => setPaused(feed, !isHeld(feed), event.currentTarget),
        },
        isHeld(feed) ? "Resume" : "Pause"
      ),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noMute ? "" : null,
          title: noMute ?? "Drop this feed and mute its token so it cannot come back",
          onclick: () => openRemoveDialog(feed, { rediscover: false }),
        },
        "Mute"
      ),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noRemove ? "" : null,
          title: noRemove ?? "Drop this feed from the fleet; it can be rediscovered",
          onclick: () => openRemoveDialog(feed, { rediscover: true }),
        },
        "Drop"
      )
    ),
    // replaceChildren() stringifies whatever it is handed, so a bare null child
    // renders the text "null" -- spread an empty list instead of passing one.
    ...(state.panelMessage || blocked
      ? [el("div", { class: "ops-inline-message", role: "status" }, state.panelMessage ?? blocked)]
      : []),
    intervalEditor(feed),
    el(
      "section",
      { class: "ops-panel-section" },
      el("h3", {}, "Operational details"),
      el(
        "dl",
        { class: "ops-detail-grid" },
        detailStat("Last poll", relativeTime(feed.policy?.last_poll_at), exactTime(feed.policy?.last_poll_at)),
        detailStat(
          "Next due",
          isHeld(feed) ? "Paused" : relativeTime(feed.policy?.due_at),
          exactTime(feed.policy?.due_at)
        ),
        detailStat("Last result", lastResult),
        detailStat("New · 7d", formatCount(feed.telemetry?.new_7d)),
        detailStat("Open postings", formatCount(feed.postings?.open_postings)),
        detailStat("p95 discovery", formatDuration(feed.postings?.p95_latency_s)),
        detailStat("Strikes", formatCount(feed.policy?.strikes)),
        detailStat("Conditional GET", feed.policy?.has_etag ? "ETag stored" : "No validator")
      )
    ),
    el(
      "section",
      { class: "ops-panel-section" },
      el("h3", {}, "New postings by day · 14d"),
      spark.length
        ? el(
            "div",
            {},
            sparkBars(spark.map((value, index) => ({
              value,
              label: `${labels[index]} · ${value} posting${value === 1 ? "" : "s"}`,
            }))),
            el("div", { class: "ops-chart-cap" }, el("span", {}, labels[0]), el("span", {}, labels.at(-1)))
          )
        : el("div", { class: "muted" }, "No poll observations recorded yet.")
    ),
    el(
      "section",
      { class: "ops-panel-section" },
      el("h3", {}, "Recent polls"),
      events?.length
        ? el(
            "ul",
            { class: "ops-events" },
            events.map((event) =>
              el(
                "li",
                {},
                el("time", { title: exactTime(event.polled_at) }, relativeTime(event.polled_at)),
                eventMark(event.status, event.status_code),
                el("span", { class: "ops-event-note" }, event.new_postings ? `${formatCount(event.new_postings)} new` : "No new postings")
              )
            )
          )
        : el("div", { class: "muted" }, "No poll observations recorded yet.")
    )
  );
}

async function loadDetail(ref, resetDraft = false) {
  const token = ++state.detailToken;
  try {
    const view = await api(`/api/scheduler/feeds/${ref}`);
    if (token !== state.detailToken || state.selectedRef !== ref) return;
    const index = state.feeds.findIndex((feed) => feed.feed_ref === ref);
    if (index >= 0) state.feeds[index] = view.feed;
    state.detailEvents = view.events ?? [];
    if (resetDraft || !state.intervalDraft?.dirty) {
      state.intervalDraft = makeIntervalDraft(view.feed.policy?.interval_s);
    }
    renderDetailPanel(view.feed, state.detailEvents);
    syncSelectedRow();
  } catch (error) {
    if (token === state.detailToken && state.selectedRef === ref) {
      state.panelMessage = `Could not refresh this feed: ${error.message}`;
      const cached = state.feeds.find((feed) => feed.feed_ref === ref);
      if (cached) renderDetailPanel(cached, state.detailEvents);
    }
  }
}

function selectFeed(ref, trigger = null) {
  const changed = state.selectedRef !== ref;
  state.selectedRef = ref;
  state.selectedTrigger = trigger ?? state.selectedTrigger;
  state.panelMessage = null;
  state.detailEvents = changed ? null : state.detailEvents;
  const feed = state.feeds.find((item) => item.feed_ref === ref);
  if (changed && feed) state.intervalDraft = makeIntervalDraft(feed.policy?.interval_s);
  // One drawer at a time: the feed details replace the groups editor.
  if (state.groupsPanelOpen) closeGroupsPanel({ restoreFocus: false });
  panelEl.hidden = false;
  syncSelectedRow();
  if (feed) renderDetailPanel(feed, state.detailEvents);
  else panelEl.replaceChildren(el("div", { class: "ops-panel-loading" }, "Loading feed details…"));
  // The drawer overlays the page, so focus moves into it (Escape closes it).
  requestAnimationFrame(() => panelEl.querySelector(".ops-close")?.focus());
  loadDetail(ref, changed).catch(() => {});
}

function closePanel() {
  state.detailToken += 1;
  state.selectedRef = null;
  state.detailEvents = null;
  state.intervalDraft = null;
  state.panelMessage = null;
  panelEl.hidden = true;
  syncSelectedRow();
  state.selectedTrigger?.focus();
  state.selectedTrigger = null;
}

async function toggleRun(button) {
  const running = state.overview?.control?.running ?? false;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = running ? "Pausing…" : "Resuming…";
  try {
    const view = await api("/api/scheduler/control", { body: { running: !running } });
    state.overview.control = view.control;
    state.overview.runner = view.runner;
    showToast(`Scheduler ${running ? "paused" : "resumed"}.`);
    renderSystem();
    renderTable({ preserveScroll: true });
    if (state.selectedRef) {
      const feed = state.feeds.find((item) => item.feed_ref === state.selectedRef);
      if (feed) renderDetailPanel(feed, state.detailEvents);
    }
  } catch (error) {
    showToast(`Could not ${running ? "pause" : "resume"} the scheduler: ${error.message}`, "error");
  } finally {
    button.textContent = original;
    renderHeader();
  }
}

async function applyInterval(feed, seconds, button) {
  const error = intervalError(seconds);
  if (error) return;
  button.disabled = true;
  button.textContent = "Applying…";
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { interval_s: seconds } });
    showToast(`Polling interval set to ${formatInterval(seconds)}.`);
    state.intervalDraft = makeIntervalDraft(seconds);
    await loadFeeds();
    if (state.selectedRef === feed.feed_ref) await loadDetail(feed.feed_ref, true);
  } catch (apiError) {
    reportFeedMessage(feed, `Could not change the interval: ${apiError.message}`, "error");
    button.disabled = false;
    button.textContent = "Apply interval";
  }
}

async function setPaused(feed, paused, button) {
  const original = button.textContent;
  button.disabled = true;
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { enabled: !paused } });
    showToast(`${feed.company_name || feed.key} ${paused ? "paused" : "resumed"}.`);
    await Promise.all([loadOverview(), loadFeeds()]);
    if (state.selectedRef === feed.feed_ref) await loadDetail(feed.feed_ref, true);
  } catch (error) {
    reportFeedMessage(feed, `Could not ${paused ? "pause" : "resume"} this feed: ${error.message}`, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

/* Production work always enters the scheduler queue. The request returns once
   the queue position is durable; progress then appears in the fetch log. */
async function fetchFeedNow(feed, button) {
  const label = feed.company_name || feed.key;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "queueing…";
  try {
    await api(`/api/scheduler/feeds/${feed.feed_ref}/fetch`, { body: {} });
    showToast(`${label} queued for the unified pipeline.`, "ok");
    await Promise.all([loadOverview(), loadFeeds()]);
    if (state.selectedRef === feed.feed_ref) await loadDetail(feed.feed_ref, true);
  } catch (error) {
    reportFeedMessage(feed, `Could not fetch this feed: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function setPausedMany(feeds, paused, label, button) {
  const verb = paused ? "Pause" : "Resume";
  const targets = feeds;
  if (!targets.length || state.pausingProviders.has(label)) return;
  if (!window.confirm(`${verb} all ${formatCount(targets.length)} ${label} feeds?`)) return;
  const original = button.textContent;
  state.pausingProviders.add(label);
  button.disabled = true;
  button.textContent = paused ? "pausing…" : "resuming…";
  try {
    const result = await setProviderPaused(label, paused);
    for (const feed of targets) {
      feed.policy.enabled = !paused;
      feed.sched.status = paused
        ? { state: "paused", detail: "feed.polling is false" }
        : { state: "unknown", detail: "Refreshing polling status" };
    }
    showToast(`${verb}d all ${formatCount(result.affected)} ${label} feeds.`);
  } catch (error) {
    showToast(`Could not ${verb.toLowerCase()} ${label}: ${error.message}`, "error");
  } finally {
    state.pausingProviders.delete(label);
    button.disabled = false;
    button.textContent = original;
    // Settle the button immediately; telemetry refresh can be much slower
    // than the policy write on a large fleet.
    renderTable({ preserveScroll: true });
    await Promise.allSettled([loadOverview(), loadFeeds()]);
    if (state.selectedRef) await loadDetail(state.selectedRef, true);
  }
}

/* Bulk fetch targets: the provider group behind its "fetch" button, otherwise every
   feed matching the current filters — collapsed or not. Collapsing is a view
   convenience and never changes what a bulk action touches. */
function fetchCandidates() {
  const pool = state.bulkScope?.feeds ?? visibleFeeds();
  return pool.filter((feed) => !isHeld(feed));
}

function bulkScopeLabel() {
  return state.bulkScope ? `${state.bulkScope.label} feeds` : "matching feeds";
}

function renderBulkDialog() {
  const progress = state.bulk;
  if (!progress) {
    const count = fetchCandidates().length;
    bulkDialogEl.replaceChildren(
      el("header", { class: "ops-dialog-header" }, el("h2", { id: "bulk-title" }, `Fetch ${bulkScopeLabel()}`)),
      el(
        "div",
        { class: "ops-dialog-body" },
        el("div", { class: "ops-bulk-count" }, formatCount(count)),
        el("p", {}, `Each unpaused ${state.bulkScope ? state.bulkScope.label : "matching"} feed will run through the production pipeline and write polling, raw-posting, and job-posting data. Vendor pacing remains active.`)
      ),
      el(
        "footer",
        { class: "ops-dialog-footer" },
        el("button", { class: "btn", type: "button", onclick: () => bulkDialogEl.close() }, "Cancel"),
        el("button", { class: "btn primary", type: "button", disabled: count ? null : "", onclick: runBulkFetch }, `Fetch ${formatCount(count)} feeds`)
      )
    );
    return;
  }

  const done = progress.phase === "done";
  const title = done
    ? progress.cancelled ? "Queueing stopped" : "Feeds queued"
    : progress.cancelled ? "Stopping…" : `Queueing ${bulkScopeLabel()}…`;
  const summary = `${formatCount(progress.completed)} of ${formatCount(progress.total)} queued`;
  bulkDialogEl.replaceChildren(
    el("header", { class: "ops-dialog-header" }, el("h2", { id: "bulk-title" }, title)),
    el(
      "div",
      { class: "ops-dialog-body" },
      el("div", { class: "ops-bulk-count" }, `${progress.completed}/${progress.total}`),
      el("p", {}, done ? summary : "Queue positions are being written. The unified scheduler owns all production work."),
      el("progress", { max: String(progress.total), value: String(progress.completed) }),
      el(
        "div",
        { class: "ops-progress-meta" },
        el("span", {}, summary),
        el("span", {}, progress.failed ? `${formatCount(progress.failed)} failed` : "No failures")
      )
    ),
    el(
      "footer",
      { class: "ops-dialog-footer" },
      done
        ? el("button", { class: "btn primary", type: "button", onclick: () => bulkDialogEl.close() }, "Done")
        : el(
            "button",
            {
              class: "btn danger",
              type: "button",
              disabled: progress.cancelled ? "" : null,
              onclick: () => {
                progress.cancelled = true;
                renderBulkDialog();
              },
            },
            progress.cancelled ? "Stopping…" : "Stop"
          )
    )
  );
}

function openBulkDialog(scope = null) {
  state.bulk = null;
  state.bulkScope = scope;
  renderBulkDialog();
  bulkDialogEl.showModal();
}

async function runBulkFetch() {
  const feeds = fetchCandidates();
  const progress = {
    phase: "running",
    total: feeds.length,
    completed: 0,
    failed: 0,
    cancelled: false,
  };
  state.bulk = progress;
  renderBulkDialog();
  let cursor = 0;

  async function worker() {
    while (cursor < feeds.length && !progress.cancelled) {
      const feed = feeds[cursor++];
      try {
        await api(`/api/scheduler/feeds/${feed.feed_ref}/fetch`, { body: {} });
      } catch {
        progress.failed += 1;
      }
      progress.completed += 1;
      renderBulkDialog();
    }
  }

  await Promise.all(Array.from({ length: Math.min(BULK_FETCH_CONCURRENCY, feeds.length) }, worker));
  progress.phase = "done";
  await Promise.allSettled([loadOverview(), loadFeeds()]);
  if (state.selectedRef) await loadDetail(state.selectedRef, false);
  renderBulkDialog();
  showToast(progress.cancelled
    ? `Queueing stopped after ${formatCount(progress.completed)} feeds.`
    : `Queued ${formatCount(progress.completed)} feeds${progress.failed ? `; ${progress.failed} failed` : ""}.`,
  progress.failed ? "error" : "ok");
}

async function refreshAll(button = null) {
  if (button) {
    button.disabled = true;
    button.textContent = "Refreshing…";
  }
  const ref = state.selectedRef;
  await Promise.allSettled([loadOverview(), loadFeeds(), loadGroups()]);
  if (ref && state.selectedRef === ref) await loadDetail(ref, false);
  if (button) {
    button.disabled = false;
    button.textContent = "Refresh";
  }
}

/* ---------- board groups: load, save, move menu, edit panel ---------- */

async function loadGroups() {
  try {
    const view = await api("/api/scheduler/groups");
    state.layout = view.groups ?? [];
    state.groupsAvailable = Boolean(view.available);
    state.groupsError = view.error ?? null;
  } catch (error) {
    state.groupsAvailable = false;
    state.groupsError = error.message;
  }
  renderTable({ preserveScroll: true });
  renderGroupsPanel();
}

/* Every edit writes the whole layout; the previous one is the undo. */
async function saveLayout(next, message = null, { undoable = true } = {}) {
  if (state.savingGroups) return null;
  const previous = state.layout;
  state.savingGroups = true;
  try {
    const view = await api("/api/scheduler/groups", {
      method: "PUT",
      body: { groups: next.map(({ id, name, sources }) => ({ id, name, sources })) },
    });
    state.layout = view.groups ?? [];
    state.groupsAvailable = true;
    state.groupsError = null;
    if (message) {
      showToast(message, "ok", undoable
        ? { label: "undo", run: () => saveLayout(previous, "Undone.", { undoable: false }) }
        : null);
    }
    return state.layout;
  } catch (error) {
    if (isDegraded(error)) {
      state.groupsAvailable = false;
      state.groupsError = error.message;
    }
    showToast(`Could not save groups: ${error.message}`, "error");
    return null;
  } finally {
    state.savingGroups = false;
    renderTable({ preserveScroll: true });
    renderGroupsPanel();
  }
}

function moveTo(source, target) {
  if (sectionOf(state.layout, source) === target) return;
  saveLayout(moveSource(state.layout, source, target), `${source} moved to ${sectionName(target)}.`);
}

function nameTaken(name, exceptId = null) {
  const folded = name.toLowerCase();
  return folded === UNGROUPED_NAME.toLowerCase() || state.layout.some(
    (group) => sectionKey(group.id) !== exceptId && group.name.toLowerCase() === folded
  );
}

/* Move menu: the row handle's popup listing every section, plus a new one. */
function openMoveMenu(source, anchor) {
  closeMoveMenu();
  state.moveMenu = { source, anchor, creating: false };
  anchor.setAttribute("aria-expanded", "true");
  renderMoveMenu();
}

function closeMoveMenu({ restoreFocus = false } = {}) {
  if (!state.moveMenu) return;
  const { anchor } = state.moveMenu;
  state.moveMenu = null;
  moveMenuEl.hidden = true;
  anchor.setAttribute("aria-expanded", "false");
  if (restoreFocus && anchor.isConnected) anchor.focus();
}

function renderMoveMenu() {
  const menu = state.moveMenu;
  const current = sectionOf(state.layout, menu.source);
  const targets = [
    ...state.layout.map((group) => [sectionKey(group.id), group.name]),
    [UNGROUPED, UNGROUPED_NAME],
  ];
  const items = targets.map(([id, name]) => el(
    "button",
    {
      class: "ops-move-item",
      type: "button",
      role: "menuitemradio",
      "aria-checked": String(id === current),
      onclick: () => {
        closeMoveMenu({ restoreFocus: true });
        moveTo(menu.source, id);
      },
    },
    el("span", { class: "ops-move-check", "aria-hidden": "true" }, id === current ? "✓" : ""),
    name
  ));
  const create = menu.creating
    ? el("input", {
        class: "ops-move-input",
        placeholder: "new group name",
        maxlength: "64",
        "aria-label": "New group name",
        oninput: (event) => event.target.removeAttribute("aria-invalid"),
        onkeydown: (event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            closeMoveMenu({ restoreFocus: true });
            return;
          }
          if (event.key !== "Enter") return;
          const name = event.target.value.trim();
          if (!name || nameTaken(name)) {
            event.target.setAttribute("aria-invalid", "true");
            return;
          }
          closeMoveMenu({ restoreFocus: true });
          saveLayout(addSection(state.layout, name, [menu.source]), `${menu.source} moved to new group ${name}.`);
        },
      })
    : el(
        "button",
        {
          class: "ops-move-item",
          type: "button",
          role: "menuitem",
          onclick: (event) => {
            // Re-rendering detaches this button; keep the outside-click
            // handler from reading that as a click elsewhere.
            event.stopPropagation();
            menu.creating = true;
            renderMoveMenu();
          },
        },
        el("span", { class: "ops-move-check", "aria-hidden": "true" }, "+"),
        "New group…"
      );
  moveMenuEl.replaceChildren(
    el("div", { class: "ops-move-title" }, `move ${menu.source} to`),
    ...items,
    el("hr"),
    create
  );
  moveMenuEl.hidden = false;
  // Below the handle, left edges aligned; above it near the window bottom.
  const rect = menu.anchor.getBoundingClientRect();
  const height = moveMenuEl.offsetHeight;
  const top = rect.bottom + 4 + height > innerHeight ? rect.top - 4 - height : rect.bottom + 4;
  moveMenuEl.style.top = `${Math.max(8, top)}px`;
  moveMenuEl.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - moveMenuEl.offsetWidth - 8))}px`;
  (moveMenuEl.querySelector("input") ?? moveMenuEl.querySelector('[aria-checked="true"]'))?.focus();
}

moveMenuEl.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const items = [...moveMenuEl.querySelectorAll(".ops-move-item")];
  const index = items.indexOf(document.activeElement);
  items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
});

/* Edit panel: the whole layout as blocks of provider chips. Drag ⠿ (or use
   the arrows) to reorder groups; drag a chip, use "+ add…", or × to refile. */
function openGroupsPanel() {
  if (state.selectedRef) closePanel();
  state.groupsPanelOpen = true;
  groupsPanelEl.hidden = false;
  editGroupsEl.setAttribute("aria-expanded", "true");
  renderGroupsPanel();
  requestAnimationFrame(() => groupsPanelEl.querySelector(".ops-close")?.focus());
}

function closeGroupsPanel({ restoreFocus = true } = {}) {
  state.groupsPanelOpen = false;
  state.renaming = null;
  groupsPanelEl.hidden = true;
  editGroupsEl.setAttribute("aria-expanded", "false");
  if (restoreFocus) editGroupsEl.focus();
}

function finishRename(id, value) {
  if (state.renaming !== id) return;
  const name = value.trim();
  if (name && name !== sectionName(id) && nameTaken(name, id)) {
    showToast(`There is already a group named ${name}.`, "error");
    return;
  }
  state.renaming = null;
  if (!name || name === sectionName(id)) {
    renderGroupsPanel();
    return;
  }
  saveLayout(renameSection(state.layout, id, name));
}

function clearDropHints() {
  for (const node of groupsPanelEl.querySelectorAll("[data-drop]")) delete node.dataset.drop;
}

function onBlockDragOver(event, id) {
  const types = event.dataTransfer.types;
  let kind = null;
  if (types.includes("text/x-provider")) kind = "into";
  else if (types.includes("text/x-group")) {
    const rect = event.currentTarget.getBoundingClientRect();
    kind = id === UNGROUPED || event.clientY < rect.top + rect.height / 2 ? "before" : "after";
  }
  if (!kind) return;
  event.preventDefault();
  if (event.currentTarget.dataset.drop !== kind) {
    clearDropHints();
    event.currentTarget.dataset.drop = kind;
  }
}

function onBlockDrop(event, id) {
  event.preventDefault();
  const where = event.currentTarget.dataset.drop;
  const source = event.dataTransfer.getData("text/x-provider");
  const moving = event.dataTransfer.getData("text/x-group");
  clearDropHints();
  if (source) {
    moveTo(source, id);
    return;
  }
  if (!moving || moving === id) return;
  const rest = state.layout.filter((group) => sectionKey(group.id) !== moving);
  const index = id === UNGROUPED
    ? rest.length
    : rest.findIndex((group) => sectionKey(group.id) === id) + (where === "after" ? 1 : 0);
  saveLayout(moveSection(state.layout, moving, index));
}

function memberChip(source, id, worst) {
  return el(
    "span",
    {
      class: "ops-member",
      draggable: "true",
      title: `${source} · drag to another group`,
      ondragstart: (event) => {
        event.stopPropagation();
        event.dataTransfer.setData("text/x-provider", source);
        event.dataTransfer.effectAllowed = "move";
        event.currentTarget.classList.add("ops-dragging");
      },
      ondragend: (event) => {
        event.currentTarget.classList.remove("ops-dragging");
        clearDropHints();
      },
    },
    worst === "backing_off" || worst === "stale"
      ? el("span", { class: "ops-member-dot", dataset: { state: worst } })
      : null,
    source,
    id === UNGROUPED
      ? null
      : el(
          "button",
          {
            class: "ops-member-remove",
            type: "button",
            title: `Move to ${UNGROUPED_NAME}`,
            "aria-label": `Move ${source} to ${UNGROUPED_NAME}`,
            onclick: () => moveTo(source, UNGROUPED),
          },
          "×"
        )
  );
}

function addInput(id, name) {
  const tryAdd = (input) => {
    const source = input.value.trim();
    if (!knownSources().includes(source)) {
      input.setAttribute("aria-invalid", "true");
      return;
    }
    input.value = "";
    moveTo(source, id);
  };
  return el("input", {
    class: "ops-member-add",
    list: "groups-sources",
    placeholder: "+ add…",
    autocomplete: "off",
    "aria-label": `Add a provider to ${name}`,
    onkeydown: (event) => {
      if (event.key === "Enter") tryAdd(event.target);
    },
    // Picking from the suggestion list files the provider straight away.
    oninput: (event) => {
      event.target.removeAttribute("aria-invalid");
      if ((!event.inputType || event.inputType === "insertReplacementText") &&
          knownSources().includes(event.target.value)) {
        tryAdd(event.target);
      }
    },
  });
}

function knownSources() {
  return [...new Set([
    ...state.feeds.map((feed) => feed.source),
    ...state.layout.flatMap((group) => group.sources),
  ])].sort();
}

function groupBlock(section, index, count, worstBySource) {
  const { id, name, ungrouped } = section;
  const iconButton = (label, title, text, onclick, disabled = false) => el(
    "button",
    { class: "ops-icon-btn", type: "button", title, "aria-label": label, disabled, onclick },
    text
  );
  const title = state.renaming === id
    ? el("input", {
        class: "ops-groups-name-input",
        value: name,
        maxlength: "64",
        "aria-label": "Group name",
        onkeydown: (event) => {
          if (event.key === "Enter") finishRename(id, event.target.value);
          if (event.key === "Escape") {
            event.stopPropagation();
            state.renaming = null;
            renderGroupsPanel();
          }
        },
        onblur: (event) => finishRename(id, event.target.value),
      })
    : el("span", { class: "ops-groups-name" }, name);
  const head = el(
    "div",
    { class: "ops-groups-head" },
    ungrouped
      ? el("span", { class: "ops-groups-grip", "aria-hidden": "true" })
      : el(
          "span",
          {
            class: "ops-groups-grip",
            draggable: "true",
            title: "Drag to reorder",
            "aria-hidden": "true",
            ondragstart: (event) => {
              const block = event.currentTarget.closest(".ops-groups-block");
              event.dataTransfer.setData("text/x-group", id);
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setDragImage(block, 12, 12);
              block.classList.add("ops-dragging");
            },
            ondragend: (event) => {
              event.currentTarget.closest(".ops-groups-block")?.classList.remove("ops-dragging");
              clearDropHints();
            },
          },
          "⠿"
        ),
    title,
    el("span", { class: "ops-groups-count" }, formatCount(section.sources.length)),
    ungrouped ? el("span", { class: "ops-groups-note" }, "· new providers land here") : null,
    ungrouped
      ? null
      : el(
          "span",
          { class: "ops-groups-tools" },
          iconButton(`Rename ${name}`, "Rename", "✎", () => {
            state.renaming = id;
            renderGroupsPanel();
          }),
          iconButton(`Move ${name} up`, "Move up", "↑",
            () => saveLayout(moveSection(state.layout, id, index - 1)), index === 0),
          iconButton(`Move ${name} down`, "Move down", "↓",
            () => saveLayout(moveSection(state.layout, id, index + 1)), index === count - 1),
          iconButton(`Delete ${name}`, `Delete group (its providers go to ${UNGROUPED_NAME})`, "✕",
            () => saveLayout(
              removeSection(state.layout, id),
              `Deleted ${name}; ${formatCount(section.sources.length)} providers moved to ${UNGROUPED_NAME}.`
            ))
        )
  );
  return el(
    "section",
    {
      class: "ops-groups-block",
      dataset: { id },
      ondragover: (event) => onBlockDragOver(event, id),
      ondragleave: (event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) delete event.currentTarget.dataset.drop;
      },
      ondrop: (event) => onBlockDrop(event, id),
    },
    head,
    el(
      "div",
      { class: "ops-groups-members" },
      ...section.sources.map((source) => memberChip(source, id, worstBySource.get(source))),
      ungrouped ? null : addInput(id, name)
    )
  );
}

function renderGroupsPanel() {
  if (!state.groupsPanelOpen) return;
  const providers = groupFeeds(state.feeds, { view: "all", search: "" });
  const worstBySource = new Map(providers.map((group) => [group.source, group.rollup.worst]));
  const filed = new Set(state.layout.flatMap((group) => group.sources));
  const sections = [
    ...state.layout.map((group) => ({ id: sectionKey(group.id), name: group.name, ungrouped: false, sources: group.sources })),
    // Ungrouped lists biggest first, the same order the board uses.
    { id: UNGROUPED, name: UNGROUPED_NAME, ungrouped: true,
      sources: providers.map((group) => group.source).filter((source) => !filed.has(source)) },
  ];
  const blocked = groupsBlocked();
  groupsPanelEl.replaceChildren(
    el(
      "header",
      { class: "ops-panel-header" },
      el(
        "div",
        { class: "ops-panel-title" },
        el("div", { class: "ops-panel-title-row" }, el("h2", { id: "groups-title" }, "Groups")),
        el("p", {}, "top to bottom is the order on the board")
      ),
      el("button", { class: "ops-close", type: "button", "aria-label": "Close groups", onclick: () => closeGroupsPanel() }, "×")
    ),
    ...(blocked
      ? [el("p", { class: "ops-inline-message" }, blocked)]
      : [
          ...sections.map((section, index) => groupBlock(section, index, state.layout.length, worstBySource)),
          el(
            "div",
            { class: "ops-groups-foot" },
            el(
              "button",
              {
                class: "ops-linkish",
                type: "button",
                onclick: async () => {
                  const saved = await saveLayout(addSection(state.layout, uniqueSectionName(state.layout)));
                  if (!saved) return;
                  state.renaming = sectionKey(saved.at(-1).id);
                  renderGroupsPanel();
                },
              },
              "+ new group"
            )
          ),
          el("datalist", { id: "groups-sources" },
            ...knownSources().map((source) => el("option", { value: source }, sectionName(sectionOf(state.layout, source))))),
        ])
  );
  const input = groupsPanelEl.querySelector(".ops-groups-name-input");
  if (input) {
    input.focus();
    input.select();
  }
}

document.getElementById("search").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderTable, SEARCH_DEBOUNCE_MS);
});

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    renderViewCounts();
    renderTable();
  });
}

for (const th of document.querySelectorAll("#sched-table th[data-sort]")) {
  th.querySelector("button").addEventListener("click", () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) {
      state.sort = { key, direction: state.sort.direction === "asc" ? "desc" : "asc" };
    } else {
      state.sort = { key, direction: "asc" };
    }
    renderTable();
  });
}

runToggleEl.addEventListener("click", () => toggleRun(runToggleEl));
document.getElementById("refresh").addEventListener("click", (event) => refreshAll(event.currentTarget));
fetchMatchingEl.addEventListener("click", () => openBulkDialog());
document.getElementById("expand-all").addEventListener("click", () => setAllExpanded(true));
document.getElementById("collapse-all").addEventListener("click", () => setAllExpanded(false));
editGroupsEl.addEventListener("click", () => (state.groupsPanelOpen ? closeGroupsPanel() : openGroupsPanel()));

document.addEventListener("click", (event) => {
  if (state.moveMenu && !moveMenuEl.contains(event.target)) closeMoveMenu();
});
// The menu is positioned against the viewport; anything that moves the page
// would leave it pointing at the wrong row.
addEventListener("scroll", () => closeMoveMenu(), true);
addEventListener("resize", () => closeMoveMenu());

bulkDialogEl.addEventListener("cancel", (event) => {
  if (state.bulk?.phase === "running") {
    event.preventDefault();
    state.bulk.cancelled = true;
    renderBulkDialog();
  }
});

bulkDialogEl.addEventListener("close", () => {
  if (state.bulk?.phase !== "running") {
    state.bulk = null;
    state.bulkScope = null;
  }
});

document.addEventListener("keydown", (event) => {
  const target = event.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;
  if (event.key === "/" && !editing && !bulkDialogEl.open) {
    event.preventDefault();
    document.getElementById("search").focus();
  }
  if (event.key !== "Escape" || bulkDialogEl.open) return;
  if (state.moveMenu) closeMoveMenu({ restoreFocus: true });
  else if (state.groupsPanelOpen) closeGroupsPanel();
  else if (state.selectedRef) closePanel();
});

renderClock();
renderLiveLog();

startLive();
// Groups never reject (unavailable reads as "no groups"), so they stay out
// of the all-failed check below.
loadGroups();
const initial = await Promise.allSettled([loadOverview(), loadFeeds(), loadLog()]);
if (initial.every((result) => result.status === "rejected")) {
  emptyEl.hidden = false;
  emptyEl.textContent = "Console backend unavailable. Use Refresh to try again.";
}

setInterval(() => {
  if (!document.hidden) renderClock();
}, 1000);

setInterval(() => {
  if (!document.hidden && !state.bulk) tickCountdowns();
}, COUNTDOWN_TICK_MS);

setInterval(() => {
  if (!document.hidden && !state.bulk) loadLog();
}, LOG_REFRESH_MS);

setInterval(() => {
  if (!document.hidden && !state.bulk) loadOverview().catch(() => {});
}, OVERVIEW_REFRESH_MS);

setInterval(() => {
  if (document.hidden || state.bulk) return;
  const ref = state.selectedRef;
  loadFeeds()
    .then(() => ref && state.selectedRef === ref ? loadDetail(ref, false) : null)
    .catch(() => {});
}, FEEDS_REFRESH_MS);
