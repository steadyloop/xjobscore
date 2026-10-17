import { loadReconcile } from "/static/system/reconcile.js";
/* Console page — boot and page-level state.
 *
 * Owns the header (run switch, spinner state, clock), the alert line, the
 * feeds strip, and the refresh cadences. The scheduler board lives in
 * board.js, the feed drawer in drawer.js, live workers and logs in live.js.
 *
 * Cadence is entirely operator-owned: this page reads and writes interval_s,
 * but never estimates or changes it automatically. Poll telemetry remains raw
 * production evidence, and absent observations remain unknown rather than zero.
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el, formatCount, relativeTime } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";
import {
  cancelRename,
  closeMoveMenu,
  collapseAll,
  expandAll,
  initBoard,
  loadGroups,
  loadProviders,
  moveMenuContains,
  refreshBoard,
  renderBoard,
  setSearch,
  setSort,
  setView,
  tickCountdowns,
} from "/static/system/board.js";
import { closePanel, initDrawer, loadDetail, rerenderDrawer, selectedRef } from "/static/system/drawer.js";
import { startLive } from "/static/system/live.js";
import { setPollActivity } from "/static/system/activity.js";

renderNav("Console");

const OVERVIEW_REFRESH_MS = 30_000;
const BOARD_REFRESH_MS = 60_000;
const LOG_REFRESH_MS = 5000;
const COUNTDOWN_TICK_MS = 15_000;

const state = {
  overview: null,
  connected: false,
  telemetryAvailable: false,
  overviewError: null,
  boardError: null,
  logEvents: [],
  logAvailable: false,
};

const alertEl = document.getElementById("system-alert");
// Run state is shown by the spinner beside "Activity" (animated in live.js).
const activitySpinnerEl = document.getElementById("activity-spinner");
const runToggleEl = document.getElementById("run-toggle");
const schedulerHeadEl = document.getElementById("scheduler-head");
const clockEl = document.getElementById("local-clock");
const summaryEl = document.getElementById("summary-line");
const toastEl = document.getElementById("toast");
let toastTimer = null;

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
}

function renderAlert() {
  let title = null;
  let message = null;
  let level = "warning";

  if (state.overviewError || state.boardError) {
    title = "Console data is unavailable";
    message = state.overviewError || state.boardError;
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
  alertEl.replaceChildren(el("div", {}, el("strong", {}, title), el("span", {}, message)));
}

/* `73,066 ok · 25 err · 2,945 stale · 9 new · 551 paused · 112,510 total` */
function renderSummaryLine() {
  if (!state.overview) {
    summaryEl.textContent = state.overviewError ? "Scheduler state unavailable." : "Loading scheduler state…";
    return;
  }
  const counts = state.overview.counts_by_status ?? {};
  const parts = [
    el("span", { class: "ops-sum-ok" }, `${formatCount(counts.healthy ?? 0)} ok`),
    el("span", { class: counts.backing_off ? "ops-sum-bad" : null }, `${formatCount(counts.backing_off ?? 0)} err`),
    el("span", { class: counts.stale ? "ops-sum-warn" : null }, `${formatCount(counts.stale ?? 0)} stale`),
    counts.never_polled ? el("span", {}, `${formatCount(counts.never_polled)} new`) : null,
    el("span", {}, `${formatCount(counts.paused ?? 0)} paused`),
    el("span", { class: "sys-quiet" }, `${formatCount(state.overview.feed_count ?? 0)} total`),
  ].filter(Boolean);
  summaryEl.replaceChildren(
    ...parts.flatMap((part, index) => [
      index ? el("span", { class: "ops-sum-sep", "aria-hidden": "true" }, "·") : null,
      part,
    ]).filter(Boolean)
  );
}

function renderSystem() {
  renderHeader();
  renderAlert();
  renderSummaryLine();
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

async function loadBoard() {
  try {
    await loadProviders();
    state.boardError = null;
  } catch (error) {
    state.boardError = error.message;
    if (isDegraded(error)) state.connected = false;
    throw error;
  } finally {
    renderAlert();
  }
}

function renderLiveLog() {
  setPollActivity(state.logEvents, state.logAvailable);
}

async function loadLog() {
  try {
    const view = await api("/api/scheduler/events?limit=50");
    state.logAvailable = Boolean(view.telemetry_available);
    state.logEvents = view.events ?? [];
  } catch {
    state.logAvailable = false;
  }
  renderLiveLog();
}

/* After any write: counts, open lines and the open feed all re-read. */
async function afterChange() {
  await Promise.allSettled([loadOverview(), refreshBoard()]);
}

async function refreshAll(button) {
  button.disabled = true;
  button.textContent = "Refreshing…";
  await Promise.allSettled([loadOverview(), refreshBoard()]);
  if (selectedRef()) await loadDetail();
  button.disabled = false;
  button.textContent = "Refresh";
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
    renderBoard();
    rerenderDrawer();
  } catch (error) {
    showToast(`Could not ${running ? "pause" : "resume"} the scheduler: ${error.message}`, "error");
  } finally {
    button.textContent = original;
    renderHeader();
  }
}

initDrawer({ connected: () => state.connected, toast: showToast, onChanged: afterChange });
initBoard({ connected: () => state.connected, toast: showToast, onChanged: afterChange });

document.getElementById("search").addEventListener("input", (event) => setSearch(event.target.value));

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

for (const th of document.querySelectorAll("#sched-table th[data-sort]")) {
  th.querySelector("button").addEventListener("click", () => setSort(th.dataset.sort));
}

runToggleEl.addEventListener("click", () => toggleRun(runToggleEl));
document.getElementById("refresh").addEventListener("click", (event) => refreshAll(event.currentTarget));
document.getElementById("expand-all").addEventListener("click", expandAll);
document.getElementById("collapse-all").addEventListener("click", collapseAll);

document.addEventListener("click", (event) => {
  if (!moveMenuContains(event.target)) closeMoveMenu();
});
// The menu is positioned against the viewport; scrolling would leave it
// pointing at the wrong row.
addEventListener("scroll", () => closeMoveMenu(), true);
addEventListener("resize", () => closeMoveMenu());

document.addEventListener("keydown", (event) => {
  const target = event.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;
  const dialogOpen = document.getElementById("remove-dialog").open;
  if (event.key === "/" && !editing && !dialogOpen) {
    event.preventDefault();
    document.getElementById("search").focus();
  }
  if (event.key !== "Escape" || dialogOpen) return;
  if (closeMoveMenu({ restoreFocus: true })) return;
  if (cancelRename()) return;
  if (selectedRef()) closePanel();
});

renderClock();
renderLiveLog();
renderBoard();

startLive();
// Groups never reject (unavailable reads as "no groups").
loadGroups();
const initial = await Promise.allSettled([loadOverview(), loadBoard(), loadLog()]);
if (initial.every((result) => result.status === "rejected")) {
  const emptyEl = document.getElementById("sched-empty");
  emptyEl.hidden = false;
  emptyEl.textContent = "Console backend unavailable. Use Refresh to try again.";
}

setInterval(() => {
  if (!document.hidden) renderClock();
}, 1000);

setInterval(() => {
  if (!document.hidden) tickCountdowns();
}, COUNTDOWN_TICK_MS);

setInterval(() => {
  if (!document.hidden) loadLog();
}, LOG_REFRESH_MS);

setInterval(() => {
  if (!document.hidden) loadOverview().catch(() => {});
}, OVERVIEW_REFRESH_MS);

setInterval(() => {
  if (document.hidden) return;
  refreshBoard()
    .then(() => (selectedRef() ? loadDetail() : null))
    .catch(() => {});
}, BOARD_REFRESH_MS);
