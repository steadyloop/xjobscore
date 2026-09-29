/* System page — live panels around the scheduler board.
 *
 * Three sources, three cadences:
 *   /api/system/live    every 2s  worker slots + the 15-minute feed line (process memory)
 *   /api/system/events  every 5s  system events + the last Gold run (PostgreSQL history)
 *   /api/system/logs    every 5s  server log lines after the last seen sequence number
 *
 * Worker cells are painted in place: the scheduler table rebuilds every 60s, and
 * app.js calls paintWorkers() after each rebuild so the bars never wait for a tick.
 */

import { api } from "/static/shared/api.js";
import { el, formatCount } from "/static/shared/format.js";

const LIVE_REFRESH_MS = 2000;
const HISTORY_REFRESH_MS = 5000;
const SLOW_S = 60;
const STUCK_S = 180;
const SERVER_LOG_KEPT = 500;

const live = {
  snapshot: null,
  goldLast: null,
  events: [],
  eventsAvailable: false,
  logSeq: 0,
  logBoot: null,
  logLines: [],
  logAvailable: false,
};

const stripFeedsEl = document.getElementById("strip-feeds");
const stripFeedsWindowEl = document.getElementById("strip-feeds-window");
const stripGoldEl = document.getElementById("strip-gold");
const eventsEl = document.getElementById("system-events-lines");
const serverLogEl = document.getElementById("server-log-lines");
const tableEl = document.getElementById("sched-table");
const schedulerHeadEl = document.getElementById("scheduler-head");
const spinnerEl = document.getElementById("scheduler-spinner");
const activitySpinnerEl = document.getElementById("activity-spinner");

/* ---------- formatting ---------- */

export function clockTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
}

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

function ago(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

function slotState(ageS) {
  if (ageS >= STUCK_S) return "stuck";
  if (ageS >= SLOW_S) return "slow";
  return "busy";
}

const sep = () => el("span", { class: "wk-sep" }, "·");

function counted(value, word, tone) {
  const label = `${formatCount(value)} ${value === 1 ? word.replace(/s$/, "") : word}`;
  return value
    ? el("span", { class: "ops-st", "data-state": tone }, label)
    : el("span", { class: "sys-quiet" }, label);
}

const quiet = (text) => el("span", { class: "sys-quiet" }, text);

/* ---------- status strip ---------- */

function paintFeedsLine() {
  const feeds = live.snapshot?.feeds;
  if (!feeds) {
    stripFeedsEl.replaceChildren(el("span", { class: "wk-dim" }, "live telemetry unavailable"));
    return;
  }
  // The row label names the window; until 15 minutes of uptime it is shorter.
  stripFeedsWindowEl.textContent = feeds.since_start
    ? `last ${Math.max(1, Math.floor(feeds.window_s / 60))}m`
    : "last 15m";
  stripFeedsEl.replaceChildren(
    quiet(`${formatCount(feeds.polled)} polled`), sep(),
    quiet(`${formatCount(feeds.with_new)} with new jobs`), sep(),
    el("b", {}, `${formatCount(feeds.new_postings)} new postings`), sep(),
    counted(feeds.partial, "partial", "stale"), sep(),
    counted(feeds.errors, "errors", "backing_off")
  );
}

function paintGoldLine() {
  const run = live.goldLast;
  if (!run) {
    stripGoldEl.replaceChildren(
      el("span", { class: "wk-dim" }, live.eventsAvailable ? "no finished run yet" : "history unavailable")
    );
    return;
  }
  const now = Date.now();
  const finished = new Date(run.finished_at).getTime();
  const started = new Date(run.started_at).getTime();
  const gold = run.detail?.gold ?? {};
  const counts = Object.entries(gold.counts ?? {}).filter(([, count]) => count.postings);
  const total = counts.reduce((sum, [, count]) => sum + count.postings, 0);
  const errors = Object.keys(gold.errors ?? {}).length;
  const parts = [
    "last run ", el("b", {}, `${ago((now - finished) / 1000)} ago`), sep(),
    duration((finished - started) / 1000),
  ];
  if (run.state !== "complete") {
    parts.push(sep(), el("span", {
      class: "ops-st",
      "data-state": run.state === "failed" ? "backing_off" : "stale",
    }, run.state));
  }
  parts.push(sep(), el("b", {}, `${formatCount(total)} postings enriched`));
  if (counts.length) {
    parts.push(" ", el("span", { class: "wk-dim" },
      `(${counts.map(([step, count]) => `${step} ${formatCount(count.postings)}`).join(" · ")})`));
  }
  parts.push(sep(), counted(errors, "errors", "backing_off"));
  if (run.error) {
    parts.push(sep(), el("span", { class: "wk-dim", title: run.error }, run.error.split("\n")[0]));
  }
  stripGoldEl.replaceChildren(...parts);
}

/* ---------- worker cells ---------- */

function workerBar(source, pool) {
  const busy = pool?.busy ?? [];
  const width = pool?.width ?? 1;
  const ticks = [];
  for (let index = 0; index < width; index += 1) {
    const run = busy[index];
    ticks.push(run
      ? el("i", { "data-s": slotState(run.age_s), title: `${source}:${run.key} · ${duration(run.age_s)}` })
      : el("i", { "data-s": "idle" }));
  }
  const longest = busy[0];
  const tip = `${busy.length} of ${width} workers busy`
    + (longest ? ` · longest ${longest.key} ${duration(longest.age_s)}` : "");
  return [
    el("span", { class: "sys-bar", title: tip }, ticks),
    el("span", { class: "sys-busy" }, el("b", {}, String(busy.length)), `/${width}`),
    longest && slotState(longest.age_s) !== "busy"
      ? el("span", {
          class: "wk-age",
          "data-s": slotState(longest.age_s),
          title: `${source}:${longest.key}`,
        }, duration(longest.age_s))
      : null,
  ];
}

export function paintWorkers() {
  const workers = live.snapshot?.workers;
  if (!workers || !tableEl) return;
  for (const cell of tableEl.querySelectorAll("td[data-worker-source]")) {
    // replaceChildren prints null as text, unlike el(): drop the absent age flag.
    cell.replaceChildren(
      ...workerBar(cell.dataset.workerSource, workers[cell.dataset.workerSource]).filter(Boolean)
    );
  }
  const running = new Map();
  for (const [source, pool] of Object.entries(workers)) {
    for (const run of pool.busy) running.set(`${source}:${run.key}`, run.age_s);
  }
  for (const cell of tableEl.querySelectorAll("td[data-worker-feed]")) {
    const age = running.get(cell.dataset.workerFeed);
    cell.replaceChildren(age == null
      ? ""
      : el("span", { class: "wk-age", "data-s": slotState(age) }, `polling ${duration(age)}`));
  }
}

/* ---------- system events ---------- */

const EVENT_LABEL = { gold_catchup: "GOLD", dedup: "DEDUP", token_heartbeat: "BEAT", feed: "FEED" };
const EVENT_STATUS = {
  running: "running", complete: "ok", failed: "error", degraded: "partial", interrupted: "partial",
};

function renderEvents() {
  if (!live.events.length) {
    eventsEl.replaceChildren(el("div", { class: "ops-log-empty" },
      live.eventsAvailable ? "no runs yet" : "history unavailable"));
    return;
  }
  eventsEl.replaceChildren(...live.events.map((run) => {
    const started = new Date(run.started_at).getTime();
    const finished = run.finished_at ? new Date(run.finished_at).getTime() : null;
    const what = run.job_type === "feed"
      ? run.identity
      : run.job_type === "dedup" && run.identity ? `dedup #${run.identity}` : run.job_type;
    const note = finished == null
      ? "running"
      : `${run.state} · ${duration((finished - started) / 1000)}`;
    return el("div", { class: "ops-log-line", title: run.error ?? "" },
      el("time", {}, clockTime(run.started_at)),
      el("span", { class: "ops-log-st sys-ev-st", "data-status": EVENT_STATUS[run.state] ?? "partial" },
        EVENT_LABEL[run.job_type] ?? run.job_type),
      el("span", { class: "ops-log-feed", title: run.identity ?? "" }, what ?? ""),
      el("span", { class: "ops-log-note" }, note));
  }));
}

/* ---------- server log ---------- */

const LEVEL_CLASS = { WARNING: "warn", ERROR: "error", CRITICAL: "error" };

function renderServerLog(fresh) {
  if (!live.logLines.length) {
    serverLogEl.replaceChildren(el("div", { class: "ops-log-empty" },
      live.logAvailable ? "no log lines yet" : "server log unavailable"));
    return;
  }
  if (!fresh.length && serverLogEl.querySelector(".sys-slog-line")) return;
  const pinned = serverLogEl.scrollHeight - serverLogEl.scrollTop - serverLogEl.clientHeight < 12;
  serverLogEl.replaceChildren(...live.logLines.map((line) => {
    const level = LEVEL_CLASS[line.level] ?? "info";
    return el("div", { class: "sys-slog-entry" },
      el("div", { class: "sys-slog-line", "data-level": level },
        el("time", {}, clockTime(line.ts)),
        el("span", { class: "sys-slog-lvl" }, line.level === "WARNING" ? "WARN" : line.level),
        el("span", { class: "sys-slog-name", title: line.name }, line.name),
        el("span", { class: "sys-slog-msg", title: line.msg }, line.msg)),
      line.exc
        ? el("details", { class: "sys-slog-exc" }, el("summary", {}, "traceback"), el("pre", {}, line.exc))
        : null);
  }));
  if (pinned) serverLogEl.scrollTop = serverLogEl.scrollHeight;
}

/* ---------- loaders ---------- */

async function loadLive() {
  try {
    live.snapshot = await api("/api/system/live");
  } catch {
    live.snapshot = null;
  }
  measurePollRate(live.snapshot);
  paintFeedsLine();
  paintGoldLine();
  paintWorkers();
}

async function loadEvents() {
  try {
    const view = await api("/api/system/events?limit=20");
    live.events = view.events ?? [];
    live.goldLast = view.gold_last ?? null;
    live.eventsAvailable = true;
  } catch {
    live.eventsAvailable = false;
  }
  renderEvents();
  paintGoldLine();
}

async function loadServerLog() {
  let fresh = [];
  try {
    let view = await api(`/api/system/logs?after=${live.logSeq}`);
    if (live.logBoot && view.boot !== live.logBoot) {
      // The server restarted: our cursor belongs to the previous process.
      live.logLines = [];
      view = await api("/api/system/logs?after=0");
    }
    live.logBoot = view.boot;
    fresh = view.lines ?? [];
    live.logSeq = view.last_seq;
    live.logLines = [...live.logLines, ...fresh].slice(-SERVER_LOG_KEPT);
    live.logAvailable = true;
  } catch {
    live.logAvailable = false;
  }
  renderServerLog(fresh);
}

/* ---------- spinner ---------- */

/* One frame per completed poll. Each 2s snapshot says how many polls finished
   since the last one; the spinner plays that many frames over the next 2s.
   Past ~20 frames/s braille blurs, so faster than that just means flat out. */
const SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
const SPIN_TICK_MS = 50;
const SPIN_MAX_FPS = 20;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const spin = { frame: 0, fps: 0, carry: 0, lastTotal: null, lastAt: 0 };

// Called with every live snapshot: turn the poll count delta into a frame rate.
function measurePollRate(snapshot) {
  const now = performance.now();
  const total = snapshot?.polls_total;
  if (typeof total !== "number") {
    spin.fps = 0;
    return;
  }
  if (spin.lastTotal == null || total < spin.lastTotal) {
    // First snapshot, or the server restarted and its counter reset.
    spin.fps = 0;
  } else {
    const seconds = Math.max(0.5, (now - spin.lastAt) / 1000);
    spin.fps = Math.min(SPIN_MAX_FPS, (total - spin.lastTotal) / seconds);
  }
  spin.lastTotal = total;
  spin.lastAt = now;
}

// off: polling paused or no runner (app.js sets data-live). idle: nothing in
// flight. busy: feeds in flight -- the glyph shows, turning as polls complete.
function spinnerState() {
  if (schedulerHeadEl?.dataset.live !== "on") return "off";
  const busy = Object.values(live.snapshot?.workers ?? {}).some((pool) => pool.busy.length);
  return busy || spin.fps > 0 ? "busy" : "idle";
}

/* The Activity spinner turns at a steady pace while the runtime is running;
   paused and offline hold a still glyph (colored by CSS). app.js sets data-state. */
const STEADY_EVERY = 2; // ticks per frame: 10 frames/s
let steadyTick = 0;
let steadyFrame = 0;

function tickActivitySpinner() {
  if (!activitySpinnerEl) return;
  const state = activitySpinnerEl.dataset.state;
  if (state === "running") {
    steadyTick = (steadyTick + 1) % STEADY_EVERY;
    if (!steadyTick && !reducedMotion.matches) steadyFrame = (steadyFrame + 1) % SPIN_FRAMES.length;
    activitySpinnerEl.textContent = SPIN_FRAMES[steadyFrame];
  } else {
    activitySpinnerEl.textContent = state === "loading" ? "·" : "⠿";
  }
}

function tickSpinner() {
  if (document.hidden) return;
  tickActivitySpinner();
  if (!spinnerEl) return;
  const state = spinnerState();
  spinnerEl.dataset.state = state;
  if (state !== "busy") {
    spinnerEl.textContent = "·";
    spin.carry = 0;
    return;
  }
  if (!reducedMotion.matches) {
    spin.carry += (spin.fps * SPIN_TICK_MS) / 1000;
    const steps = Math.floor(spin.carry);
    spin.carry -= steps;
    spin.frame = (spin.frame + steps) % SPIN_FRAMES.length;
  }
  spinnerEl.textContent = SPIN_FRAMES[spin.frame];
  spinnerEl.title = `${spin.fps >= SPIN_MAX_FPS ? "≥" : ""}${spin.fps.toFixed(1)} polls/s`;
}

export async function startLive() {
  setInterval(tickSpinner, SPIN_TICK_MS);
  await Promise.allSettled([loadLive(), loadEvents(), loadServerLog()]);
  setInterval(() => {
    if (!document.hidden) loadLive();
  }, LIVE_REFRESH_MS);
  setInterval(() => {
    if (document.hidden) return;
    loadEvents();
    loadServerLog();
  }, HISTORY_REFRESH_MS);
}
