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

/* A vendor whose limiter counts per partition (Workday: per data center) runs
   one pool per lane. The row keeps one bar, split into a labelled segment per
   lane; a segment turns amber once Cloudflare starts answering its lane 429. */

// Per-lane counts from the board's feed list: feeds never polled, and the
// distinct partitions sharing each `other` lane. Recounted when the feed list
// changes (`setFeeds`) or a snapshot first names a partitioned vendor.
let boardFeeds = [];
let laneCounts = null;

export function setFeeds(feeds) {
  boardFeeds = feeds;
  laneCounts = null;
}

function countLanes(workers) {
  const never = new Map();
  const shared = new Map();
  for (const [source, pool] of Object.entries(workers)) {
    if (!pool.lanes) continue;
    const others = new Set();
    for (const feed of boardFeeds) {
      if (feed.source !== source) continue;
      const value = (feed.key.split("/")[pool.partition.index - 1] ?? "").toLowerCase();
      const named = pool.partition.named.includes(value);
      if (!named) others.add(value);
      if (!feed.policy?.last_poll_at) {
        const lane = `${source}:${named ? value : "other"}`;
        never.set(lane, (never.get(lane) ?? 0) + 1);
      }
    }
    shared.set(source, others.size);
  }
  return { never, shared };
}

function laneTip(lane) {
  const parts = [`${lane.label} · ${lane.busy.length}/${lane.width} busy`];
  if (lane.budget != null) {
    parts.push(lane.rate != null ? `${lane.rate} of ${lane.budget} req/s` : `${lane.budget} req/s budget`);
  }
  const never = laneCounts?.never.get(lane.lane);
  if (never) parts.push(`${formatCount(never)} never polled`);
  if (lane.throttled != null) parts.push(`${formatCount(lane.throttled)} 429s in 1h`);
  if (lane.label === "other") parts.push("shared by data centers without their own budget");
  return parts.join(" · ");
}

function splitBar(source, pool) {
  const busy = pool.lanes.reduce((sum, lane) => sum + lane.busy.length, 0);
  const segments = pool.lanes.map((lane) => {
    const ticks = [];
    for (let index = 0; index < lane.width; index += 1) {
      const run = lane.busy[index];
      ticks.push(run
        ? el("i", { "data-s": slotState(run.age_s), title: `${source}:${run.key} · ${duration(run.age_s)}` })
        : el("i", { "data-s": "idle" }));
    }
    return el("span", {
      class: "sys-lane",
      "data-throttled": lane.throttled ? "true" : null,
      title: laneTip(lane),
    }, el("span", { class: "sys-lane-k" }, lane.label === "other"
      ? `+${laneCounts?.shared.get(source) ?? ""}`
      : lane.label), ticks);
  });
  return [
    el("span", { class: "sys-bar sys-bar-split" }, segments),
    el("span", { class: "sys-busy", title: `${busy} of ${pool.width} workers busy` },
      el("b", {}, String(busy)), `/${pool.width}`),
  ];
}

export function paintWorkers() {
  const workers = live.snapshot?.workers;
  if (!workers || !tableEl) return;
  if (!laneCounts && boardFeeds.length) laneCounts = countLanes(workers);
  for (const cell of tableEl.querySelectorAll("td[data-worker-source]")) {
    const pool = workers[cell.dataset.workerSource];
    // replaceChildren prints null as text, unlike el(): drop the absent age flag.
    cell.replaceChildren(
      ...(pool?.lanes ? splitBar(cell.dataset.workerSource, pool) : workerBar(cell.dataset.workerSource, pool))
        .filter(Boolean)
    );
  }
  // Section headings sum their providers' slots; the empty bar keeps the
  // count aligned under the provider rows' counts.
  for (const cell of tableEl.querySelectorAll("td[data-worker-sources]")) {
    let busy = 0;
    let width = 0;
    for (const source of cell.dataset.workerSources.split(" ").filter(Boolean)) {
      busy += workers[source]?.busy.length ?? 0;
      width += workers[source]?.width ?? 1;
    }
    cell.replaceChildren(
      el("span", { class: "sys-bar", "aria-hidden": "true" }),
      el("span", { class: "sys-busy", title: `${busy} of ${width} workers busy` },
        el("b", {}, String(busy)), `/${width}`)
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
