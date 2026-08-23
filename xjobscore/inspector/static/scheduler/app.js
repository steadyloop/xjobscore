/* Scheduler operations console — fleet view.
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
import { dropFeed, muteFeed } from "/static/shared/feed-policy.js";
import { renderNav } from "/static/shared/nav.js";
import { sparkBars } from "/static/shared/spark.js";
import {
  ATTENTION_STATES,
  INTERVAL_PRESETS,
  dueInfo,
  formatRequestRate,
  intervalError,
  intervalInput,
  intervalSeconds,
  jobsToday,
  matchesFeed,
  providerRollups,
  sortFeeds,
  summaryCounts,
} from "/static/scheduler/model.mjs";

renderNav("Scheduler");

const RENDER_CHUNK = 200;
const SEARCH_DEBOUNCE_MS = 180;
const OVERVIEW_REFRESH_MS = 30_000;
const FEEDS_REFRESH_MS = 60_000;
const LOG_REFRESH_MS = 5000;
const COUNTDOWN_TICK_MS = 15_000;
const POLL_NOW_SETTLE_MS = 3000;
const BULK_FETCH_CONCURRENCY = 8;

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
  provider: null,
  providersExpanded: false,
  sort: { key: "due", direction: "asc" },
  bulk: null,
  logEvents: [],
  logAvailable: false,
};

const alertEl = document.getElementById("system-alert");
const runStateEl = document.getElementById("run-state");
const runToggleEl = document.getElementById("run-toggle");
const clockEl = document.getElementById("utc-clock");
const summaryEl = document.getElementById("summary-line");
const chipsEl = document.getElementById("provider-chips");
const emptyEl = document.getElementById("sched-empty");
const countEl = document.getElementById("row-count");
const panelEl = document.getElementById("detail-panel");
const logLinesEl = document.getElementById("live-log-lines");
const logDotEl = document.getElementById("live-log-dot");
const fetchShownEl = document.getElementById("fetch-shown");
const bulkDialogEl = document.getElementById("bulk-dialog");
const toastEl = document.getElementById("toast");
let bodyEl = document.getElementById("sched-body");
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
  disabled: "off",
  unknown: "?",
};

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

function showToast(message, tone = "ok") {
  clearTimeout(toastTimer);
  toastEl.textContent = message;
  toastEl.dataset.tone = tone;
  toastEl.hidden = false;
  toastTimer = setTimeout(() => {
    toastEl.hidden = true;
  }, 4200);
}

function renderClock() {
  clockEl.textContent = `${new Date().toISOString().slice(11, 19)} UTC`;
}

function renderHeader() {
  const control = state.overview?.control;
  const runner = state.overview?.runner;
  const running = control?.running ?? false;
  const alive = Boolean(runner?.alive);

  if (!state.overview && !state.overviewError) {
    runStateEl.textContent = "Loading…";
  } else {
    const label = !running ? "paused" : alive ? "running" : "runner offline";
    runStateEl.replaceChildren(
      el("span", {
        class: "ops-status-dot",
        "data-state": running && alive ? "running" : running ? "warning" : "paused",
      }),
      el("span", {}, label)
    );
    runStateEl.title = runner?.last_cycle_at
      ? `Last cycle ${relativeTime(runner.last_cycle_at)}${runner.last_polled != null ? ` · ${formatCount(runner.last_polled)} feeds polled` : ""}`
      : running ? "No completed cycles yet" : "Production polling is stopped";
  }

  runToggleEl.disabled = !state.connected || !state.overview;
  runToggleEl.textContent = running ? "Pause" : "Resume";
  runToggleEl.classList.toggle("primary", !running && state.connected);
  runToggleEl.title = running ? "Pause all scheduled polling" : "Resume scheduled polling";
  fetchShownEl.disabled = !state.connected;
}

function renderAlert() {
  let title = null;
  let message = null;
  let level = "warning";

  if (state.overviewError || state.feedsError) {
    title = "Inspector data is unavailable";
    message = state.overviewError || state.feedsError;
    level = "error";
  } else if (!state.connected) {
    title = "PostgreSQL is unavailable";
    message = "Production state cannot be read. Scheduler controls are disabled until the connection returns.";
    level = "error";
  } else if (state.overview?.control?.running && !state.overview?.runner?.alive) {
    title = "No scheduler runner is listening";
    message = "The run switch is on, but this Inspector process is not supervising production polling.";
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
  const providers = new Set(state.feeds.map((feed) => feed.source)).size;
  const parts = [
    `${formatCount(state.overview.feed_count)} sources${providers ? ` / ${formatCount(providers)} providers` : ""}`,
    el("span", { class: "ops-sum-ok" }, `${formatCount(counts.ok)} ok`),
    el("span", { class: counts.erroring ? "ops-sum-bad" : null }, `${formatCount(counts.erroring)} erroring`),
    `${formatCount(counts.paused)} paused`,
  ];
  if (counts.other) parts.push(`${formatCount(counts.other)} stale/unpolled`);
  const tiles = state.overview.tiles;
  if (tiles) parts.push(`${formatCount(tiles.new_postings_today)} jobs today`);
  summaryEl.replaceChildren(
    ...parts.flatMap((part, index) => [
      index ? el("span", { class: "ops-sum-sep", "aria-hidden": "true" }, "·") : null,
      typeof part === "string" ? el("span", {}, part) : part,
    ]).filter(Boolean)
  );
}

/* One grid row holds the toggle plus this many chips. */
const PROVIDER_COLLAPSE_COUNT = 9;

function renderProviderChips() {
  if (!state.feeds.length) {
    chipsEl.replaceChildren();
    return;
  }
  const rollups = providerRollups(state.feeds, state.overview?.tiles?.error_sources ?? []);
  if (state.provider && !rollups.some((item) => item.source === state.provider)) {
    state.provider = null;
  }
  const collapsed = !state.providersExpanded && rollups.length > PROVIDER_COLLAPSE_COUNT + 1;
  let visible = rollups;
  if (collapsed) {
    visible = rollups.slice(0, PROVIDER_COLLAPSE_COUNT);
    const selected = rollups.find((item) => item.source === state.provider);
    if (selected && !visible.includes(selected)) visible[visible.length - 1] = selected;
  }
  const toggle = collapsed || state.providersExpanded
    ? el(
        "button",
        {
          class: "ops-chip-more",
          type: "button",
          "aria-expanded": String(state.providersExpanded),
          onclick: () => {
            state.providersExpanded = !state.providersExpanded;
            renderProviderChips();
          },
        },
        collapsed ? `+${formatCount(rollups.length - visible.length)} more` : "less"
      )
    : null;
  chipsEl.replaceChildren(
    ...visible.map((item) =>
      el(
        "button",
        {
          class: "ops-provider-chip",
          type: "button",
          "aria-pressed": String(state.provider === item.source),
          title: [
            `${formatCount(item.total)} feeds`,
            item.paused ? `${formatCount(item.paused)} paused` : null,
            `${formatRequestRate(item.requests)} req/day configured`,
            item.errors ? `${formatCount(item.errors)} failed polls · 24h` : null,
          ].filter(Boolean).join(" · "),
          onclick: () => {
            state.provider = state.provider === item.source ? null : item.source;
            renderProviderChips();
            renderTable();
          },
        },
        el("span", { class: "ops-chip-name" }, item.source),
        el("span", { class: "ops-chip-count" }, formatCount(item.total))
      )
    ),
    toggle
  );
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
  renderProviderChips();
  renderViewCounts();
}

async function loadOverview() {
  try {
    const view = await api("/api/scheduler/overview");
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
    state.connected = Boolean(view.postgres?.connected);
    state.telemetryAvailable = Boolean(view.telemetry_available);
    state.feedsError = null;
    state.lastFeedUpdate = new Date();
    renderSummaryLine();
    renderProviderChips();
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

const LOG_STATUS = {
  ok: "OK",
  error: "ERR",
  not_modified: "304",
  partial: "PART",
};

function renderLiveLog() {
  logDotEl.dataset.state = state.logAvailable ? "live" : "off";
  if (!state.logEvents.length) {
    logLinesEl.replaceChildren(
      el("div", { class: "ops-log-empty" },
        state.logAvailable ? "waiting for polls…" : "telemetry unavailable")
    );
    return;
  }
  const pinned = logLinesEl.scrollHeight - logLinesEl.scrollTop - logLinesEl.clientHeight < 12;
  logLinesEl.replaceChildren(
    ...[...state.logEvents].reverse().map((event) =>
      el(
        "div",
        { class: "ops-log-line" },
        el("time", { title: exactTime(event.polled_at) },
          (event.polled_at ?? "").slice(11, 19)),
        el("span", { class: "ops-log-st", "data-status": event.status },
          LOG_STATUS[event.status] ?? event.status),
        el("span", { class: "ops-log-feed", title: `${event.source}:${event.key}` }, event.key),
        el("span", { class: "ops-log-note" },
          event.new_postings
            ? `${formatCount(event.new_postings)} new`
            : event.status === "not_modified" ? "not modified" : "no new")
      )
    )
  );
  if (pinned) logLinesEl.scrollTop = logLinesEl.scrollHeight;
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
  return {
    search: state.search,
    view: state.view,
    provider: state.provider,
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
  const blocked = pollNowBlocked(feed);
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
        title: blocked ?? "Put this feed first in the production queue",
        onclick: (event) => pollNow(feed, event.currentTarget),
      },
      "poll"
    ),
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: canEdit ? null : "",
        onclick: (event) => setPaused(feed, !feed.policy?.paused_at, event.currentTarget),
      },
      feed.policy?.paused_at ? "resume" : "pause"
    )
  );
}

function feedRow(feed, nowMs) {
  const status = feed.sched.status;
  const name = feed.company_name || feed.key;
  const due = dueInfo(feed.policy?.due_at, feed.policy?.paused_at, nowMs);
  const jobs = jobsToday(feed);
  const errs = feed.telemetry?.error_count_7d ?? 0;
  return el(
    "tr",
    {
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
    el("td", { class: "ops-provider-cell" }, feed.source),
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

function syncSelectedRow() {
  for (const row of bodyEl.querySelectorAll("tr[data-feed-ref]")) {
    row.setAttribute("aria-selected", String(row.dataset.feedRef === state.selectedRef));
  }
}

function renderTable({ preserveScroll = false } = {}) {
  const now = Date.now();
  const rows = sortFeeds(matchingFeeds(), state.sort, now);
  countEl.textContent = `${formatCount(rows.length)}/${formatCount(state.feeds.length)} shown`;
  renderSortHeaders();

  emptyEl.hidden = rows.length > 0;
  if (!rows.length) {
    emptyEl.textContent = state.feedsError
      ? `Could not load feeds: ${state.feedsError}`
      : state.feeds.length
        ? "No feeds match the current view. Clear the search, view, and provider filters to see the full fleet."
        : "No feeds are enabled. Enable a feed from the Feeds page to schedule it.";
  }

  const cursor = document.createElement("tbody");
  cursor.id = "sched-body";
  const scrollState = preserveScroll ? {
    windowX: window.scrollX,
    windowY: window.scrollY,
    tableLeft: tableWrapEl.scrollLeft,
    height: tableWrapEl.offsetHeight,
  } : null;
  if (scrollState) tableWrapEl.style.minHeight = `${scrollState.height}px`;
  bodyEl.replaceWith(cursor);
  bodyEl = cursor;

  let index = 0;
  function step() {
    if (!cursor.isConnected) return;
    cursor.append(...rows.slice(index, index + RENDER_CHUNK).map((feed) => feedRow(feed, now)));
    index += RENDER_CHUNK;
    if (index < rows.length) {
      requestAnimationFrame(step);
      return;
    }
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
  for (const cell of bodyEl.querySelectorAll("td[data-due-at]")) {
    const info = dueInfo(cell.dataset.dueAt, null, now);
    const label = cell.querySelector("span");
    if (label) label.textContent = info.text;
    cell.classList.toggle("ops-imminent", info.imminent);
  }
}

function dayLabels(count) {
  const labels = [];
  for (let offset = count - 1; offset >= 0; offset -= 1) {
    const date = new Date();
    date.setUTCDate(date.getUTCDate() - offset);
    labels.push(date.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
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
    el("p", { class: "ops-help" }, "Changes future cadence only. Use Poll now when this feed should run immediately.")
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

function pollNowBlocked(feed) {
  if (!state.connected) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres) return "Import this feed before polling it.";
  if (feed.policy?.enabled === false) return "Enable this feed on the Feeds page first.";
  if (feed.policy?.paused_at) return "Resume this feed before polling it now.";
  if (feed.policy?.hold_until && new Date(feed.policy.hold_until) > new Date()) {
    return "A poll of this feed is already running.";
  }
  if (!state.overview?.control?.running) return "Resume the scheduler before polling now.";
  if (!state.overview?.runner?.alive) return "No scheduler runner is listening.";
  return null;
}

/* Drop and mute both remove the feed; mute additionally mutes the discovery
   token so the heartbeat cannot promote it back. Panel-only on purpose: they
   are irreversible, so they stay behind an opened feed rather than sitting in
   every row of the board. */
function removeBlocked(feed) {
  if (!state.connected) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres || !feed.policy?.feed_id) {
    return "Not in the feed table — nothing to remove.";
  }
  return null;
}

function muteBlocked(feed) {
  if (feed.category !== "ATS") return "Mute is ATS-only.";
  return removeBlocked(feed);
}

async function removeFeed(feed, button, { verb, past, gerund, confirmation, run }) {
  const label = feed.company_name || feed.key;
  if (!window.confirm(confirmation)) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = `${gerund}…`;
  try {
    await run(feed);
    if (state.selectedRef === feed.feed_ref) closePanel();
    await Promise.all([loadOverview(), loadFeeds()]);
    showToast(`${label} ${past}.`);
  } catch (error) {
    reportFeedMessage(feed, `Could not ${verb} this feed: ${error.message}`, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

function dropSelected(feed, button) {
  const label = feed.company_name || feed.key;
  return removeFeed(feed, button, {
    verb: "drop",
    past: "dropped",
    gerund: "Dropping",
    confirmation:
      `Drop ${label} (${feed.source}/${feed.key})? ` +
      "This removes its feed and poll history, but leaves the token unmuted " +
      "and eligible for future promotion.",
    run: dropFeed,
  });
}

function muteSelected(feed, button) {
  const label = feed.company_name || feed.key;
  return removeFeed(feed, button, {
    verb: "mute",
    past: "muted",
    gerund: "Muting",
    confirmation:
      `Mute ${label} (${feed.source}/${feed.key})? ` +
      "This removes its feed and prevents automatic promotion from adding " +
      "it back. Existing job postings are kept.",
    run: muteFeed,
  });
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
  const blocked = pollNowBlocked(feed);
  const noDrop = removeBlocked(feed);
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
          title: blocked ?? "Put this feed first in the production queue",
          onclick: (event) => pollNow(feed, event.currentTarget),
        },
        "Poll now"
      ),
      el(
        "button",
        {
          class: "btn",
          type: "button",
          disabled: state.connected && feed.policy?.in_postgres ? null : "",
          onclick: (event) => setPaused(feed, !feed.policy?.paused_at, event.currentTarget),
        },
        feed.policy?.paused_at ? "Resume" : "Pause"
      ),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noDrop ? "" : null,
          title: noDrop ?? "Drop this feed without muting its token",
          onclick: (event) => dropSelected(feed, event.currentTarget),
        },
        "Drop"
      ),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noMute ? "" : null,
          title: noMute ?? "Mute token and remove feed",
          onclick: (event) => muteSelected(feed, event.currentTarget),
        },
        "Mute"
      )
    ),
    state.panelMessage || blocked
      ? el("div", { class: "ops-inline-message", role: "status" }, state.panelMessage ?? blocked)
      : null,
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
          feed.policy?.paused_at ? "Paused" : relativeTime(feed.policy?.due_at),
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
                statusChip(event.status),
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
  panelEl.hidden = false;
  syncSelectedRow();
  if (feed) renderDetailPanel(feed, state.detailEvents);
  else panelEl.replaceChildren(el("div", { class: "ops-panel-loading" }, "Loading feed details…"));
  if (matchMedia("(max-width: 1100px)").matches) {
    requestAnimationFrame(() => panelEl.querySelector(".ops-close")?.focus());
  }
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
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { paused } });
    showToast(`${feed.company_name || feed.key} ${paused ? "paused" : "resumed"}.`);
    await Promise.all([loadOverview(), loadFeeds()]);
    if (state.selectedRef === feed.feed_ref) await loadDetail(feed.feed_ref, true);
  } catch (error) {
    reportFeedMessage(feed, `Could not ${paused ? "pause" : "resume"} this feed: ${error.message}`, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

async function pollNow(feed, button) {
  const original = button.textContent;
  button.disabled = true;
  try {
    await api(`/api/scheduler/feeds/${feed.feed_ref}/poll-now`, { body: {} });
    if (state.selectedRef === feed.feed_ref) {
      state.panelMessage = "Polling now. The result will appear here when the production fetch completes.";
      renderDetailPanel(feed, state.detailEvents);
    }
    showToast(`${feed.company_name || feed.key} moved to the front of the queue.`);
    setTimeout(async () => {
      await Promise.allSettled([loadOverview(), loadFeeds()]);
      if (state.selectedRef === feed.feed_ref) await loadDetail(feed.feed_ref, false);
    }, POLL_NOW_SETTLE_MS);
  } catch (error) {
    reportFeedMessage(feed, `Could not poll this feed: ${error.message}`, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

function fetchCandidates() {
  return visibleFeeds().filter((feed) => !feed.policy?.paused_at);
}

function renderBulkDialog() {
  const progress = state.bulk;
  if (!progress) {
    const count = fetchCandidates().length;
    bulkDialogEl.replaceChildren(
      el("header", { class: "ops-dialog-header" }, el("h2", { id: "bulk-title" }, "Fetch shown feeds")),
      el(
        "div",
        { class: "ops-dialog-body" },
        el("div", { class: "ops-bulk-count" }, formatCount(count)),
        el("p", {}, "Each shown, unpaused feed will run through the production pipeline and write polling, raw-posting, and job-posting data. Vendor pacing remains active.")
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
    ? progress.cancelled ? "Fetch stopped" : "Fetch complete"
    : progress.cancelled ? "Stopping fetches…" : "Fetching shown feeds…";
  const summary = `${formatCount(progress.completed)} of ${formatCount(progress.total)} completed`;
  bulkDialogEl.replaceChildren(
    el("header", { class: "ops-dialog-header" }, el("h2", { id: "bulk-title" }, title)),
    el(
      "div",
      { class: "ops-dialog-body" },
      el("div", { class: "ops-bulk-count" }, `${progress.completed}/${progress.total}`),
      el("p", {}, done ? summary : "Production writes are in progress. Stopping prevents new fetches; in-flight requests still finish."),
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

function openBulkDialog() {
  state.bulk = null;
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
        const result = await api(`/api/scheduler/feeds/${feed.feed_ref}/fetch`, { body: {} });
        if (result?.status === "error") progress.failed += 1;
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
    ? `Fetch stopped after ${formatCount(progress.completed)} feeds.`
    : `Fetched ${formatCount(progress.completed)} feeds${progress.failed ? `; ${progress.failed} failed` : ""}.`,
  progress.failed ? "error" : "ok");
}

async function refreshAll(button = null) {
  if (button) {
    button.disabled = true;
    button.textContent = "Refreshing…";
  }
  const ref = state.selectedRef;
  await Promise.allSettled([loadOverview(), loadFeeds()]);
  if (ref && state.selectedRef === ref) await loadDetail(ref, false);
  if (button) {
    button.disabled = false;
    button.textContent = "Refresh";
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
fetchShownEl.addEventListener("click", openBulkDialog);

bulkDialogEl.addEventListener("cancel", (event) => {
  if (state.bulk?.phase === "running") {
    event.preventDefault();
    state.bulk.cancelled = true;
    renderBulkDialog();
  }
});

bulkDialogEl.addEventListener("close", () => {
  if (state.bulk?.phase !== "running") state.bulk = null;
});

document.addEventListener("keydown", (event) => {
  const target = event.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;
  if (event.key === "/" && !editing && !bulkDialogEl.open) {
    event.preventDefault();
    document.getElementById("search").focus();
  }
  if (event.key === "Escape" && state.selectedRef && !bulkDialogEl.open) closePanel();
});

renderClock();
renderLiveLog();

const initial = await Promise.allSettled([loadOverview(), loadFeeds(), loadLog()]);
if (initial.every((result) => result.status === "rejected")) {
  emptyEl.hidden = false;
  emptyEl.textContent = "Inspector backend unavailable. Use Refresh to try again.";
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
