/* System page — live panels around the scheduler board.
 *
 * Three sources, three cadences:
 *   /api/system/live    every 2s  worker slots + the 15-minute feed line (process memory)
 *   /api/system/events  every 5s  system events + the last Gold run (PostgreSQL history)
 *   /api/system/logs    every 5s  server log lines after the last seen sequence number
 *
 * The workers block above the board is repainted with every snapshot. Feed
 * rows the board draws carry a "polling Ns" slot, which board.js asks this
 * module to fill after each render (paintFeedWorkers) so it never waits a tick.
 */

import { api } from "/static/shared/api.js";
import { el, formatCount } from "/static/shared/format.js";
import { setEnrichmentWorker, setRunActivity, setServerActivity } from "/static/system/activity.js";

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
const tableEl = document.getElementById("sched-table");
const workersEl = document.getElementById("workers");
const schedulerHeadEl = document.getElementById("scheduler-head");
const spinnerEl = document.getElementById("scheduler-spinner");
const activitySpinnerEl = document.getElementById("activity-spinner");

/* ---------- formatting ---------- */

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
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

/* ---------- workers block ---------- */

/* Only vendors with a real pool appear: a one-worker vendor's single tick
   flips every few seconds and says nothing about load, and a slow poll there
   already shows as "polling Ns" on its feed row. Ticks run longest poll
   first, so stuck and slow slots sit at the left of each bar. */

function ticks(source, runs, width) {
  const out = [];
  for (let index = 0; index < width; index += 1) {
    const run = runs[index];
    out.push(run
      ? el("i", { "data-s": slotState(run.age_s), title: `${source}:${run.key} · ${duration(run.age_s)}` })
      : el("i", { "data-s": "idle" }));
  }
  return el("span", { class: "sys-bar" }, out);
}

const byAge = (runs) => [...runs].sort((a, b) => b.age_s - a.age_s);

function laneTip(lane) {
  const parts = [`${lane.busy.length}/${lane.width} busy`];
  if (lane.budget != null) {
    parts.push(lane.rate != null ? `${lane.rate} of ${lane.budget} req/s` : `${lane.budget} req/s budget`);
  }
  if (lane.throttled) parts.push(`${formatCount(lane.throttled)} 429s in 1h`);
  if (lane.label === "other") parts.push("data centers without their own budget");
  return parts.join(" · ");
}

/* A vendor whose limiter counts per partition (Workday: per data center)
   runs one pool per lane. Its row keeps one combined bar; the lanes open in
   a popup, each with its label to the right of its own bar. */
function lanePopup(source, pool) {
  return el(
    "span",
    { class: "sys-lanes", role: "tooltip" },
    pool.lanes.flatMap((lane) => [
      ticks(source, byAge(lane.busy), lane.width),
      el(
        "span",
        { class: "sys-lane-k", "data-throttled": lane.throttled ? "true" : null, title: laneTip(lane) },
        lane.label,
        lane.throttled ? " · 429" : ""
      ),
      el("span", { class: "sys-busy" }, el("b", {}, String(lane.busy.length)), `/${lane.width}`),
    ])
  );
}

function workerRow(source, pool) {
  const runs = byAge(pool.busy);
  const longest = runs[0];
  const flagged = longest && slotState(longest.age_s) !== "busy";
  const bar = ticks(source, runs, pool.width);
  return [
    el("span", { class: "sys-wk-name" }, source),
    pool.lanes
      ? el(
          "button",
          { class: "sys-lane-wrap", type: "button", "aria-label": `${source} lanes`, onclick: (event) => event.currentTarget.classList.toggle("is-open") },
          bar,
          el("span", { class: "sys-lane-caret", "aria-hidden": "true" }, "▾"),
          lanePopup(source, pool)
        )
      : bar,
    el("span", { class: "sys-busy" }, el("b", {}, String(runs.length)), `/${pool.width}`),
    flagged
      ? el("span", { class: "wk-age", "data-s": slotState(longest.age_s), title: `${source}:${longest.key}` },
          `${longest.key} ${duration(longest.age_s)}`)
      : el("span", {}),
  ];
}

function paintWorkers() {
  if (!workersEl) return;
  const workers = live.snapshot?.workers;
  if (!workers) {
    workersEl.replaceChildren(el("span", { class: "sys-strip-k" }, "workers"), el("span", { class: "wk-dim sys-wk-none" }, "live telemetry unavailable"));
    return;
  }
  // An open lane popup survives the 2s repaint.
  const openLanes = new Set(
    [...workersEl.querySelectorAll(".sys-lane-wrap.is-open")].map((node) => node.getAttribute("aria-label"))
  );
  const pools = Object.entries(workers)
    .filter(([, pool]) => pool.width > 1)
    .sort(([a, left], [b, right]) => right.width - left.width || a.localeCompare(b));
  workersEl.replaceChildren(
    ...pools.flatMap(([source, pool], index) => [
      el("span", { class: "sys-strip-k" }, index ? "" : "workers"),
      ...workerRow(source, pool),
    ])
  );
  for (const node of workersEl.querySelectorAll(".sys-lane-wrap")) {
    if (openLanes.has(node.getAttribute("aria-label"))) node.classList.add("is-open");
  }
}

/* "polling 1m04s" on whichever feed rows the board currently draws. */
export function paintFeedWorkers() {
  const workers = live.snapshot?.workers;
  if (!tableEl) return;
  const running = new Map();
  for (const [source, pool] of Object.entries(workers ?? {})) {
    for (const run of pool.busy) running.set(`${source}:${run.key}`, run.age_s);
  }
  for (const slot of tableEl.querySelectorAll("[data-worker-feed]")) {
    const age = running.get(slot.dataset.workerFeed);
    slot.replaceChildren(age == null
      ? ""
      : el("span", { class: "wk-age", "data-s": slotState(age) }, `polling ${duration(age)}`));
  }
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
  setEnrichmentWorker(live.snapshot?.enrichment ?? null);
  paintWorkers();
  paintFeedWorkers();
}

async function loadEvents() {
  try {
    const view = await api("/api/system/events?limit=20");
    live.events = view.events ?? [];
    live.goldLast = view.gold_last ?? null;
    live.eventsAvailable = view.telemetry_available !== false;
  } catch {
    live.eventsAvailable = false;
  }
  setRunActivity(live.events, live.goldLast, live.eventsAvailable);
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
  setServerActivity(live.logLines, live.logAvailable, live.logBoot);
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
