/* Live unified-runtime status. Motion is derived from fresh process state. */

import { api } from "/static/shared/api.js";

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const numberFormat = new Intl.NumberFormat();
let snapshot = null;

function normalize(raw) {
  const observedAt = raw.observed_at;
  const scheduler = raw.scheduler ?? {};
  const pipeline = raw.pipeline ?? {};
  const heartbeat = raw.heartbeat ?? {};
  const recent = pipeline.recent_runs ?? [];
  const lastWrite = recent.find((run) => run.finished_at)?.finished_at ?? null;
  return {
    observedAt,
    pipeline: {
      enabled: Boolean(scheduler.control?.running),
      runnerOnline: Boolean(scheduler.alive),
      cycleInFlight: Number(pipeline.active_runs ?? 0) > 0,
      runnerSeenAt: scheduler.alive ? observedAt : null,
      lastWriteAt: lastWrite,
      activeFeeds: pipeline.active_feeds ?? 0,
      outputToday: pipeline.output_today ?? 0,
    },
    scheduler: {
      enabled: Boolean(scheduler.control?.running),
      runnerOnline: Boolean(scheduler.alive),
      runnerSeenAt: scheduler.alive ? observedAt : null,
      lastTickAt: scheduler.last_cycle_at,
      cadenceSeconds: scheduler.cadence_seconds ?? 60,
      dueFeeds: scheduler.due_feeds ?? 0,
    },
    heartbeat: {
      configured: Boolean(heartbeat.configured),
      paused: Boolean(heartbeat.paused),
      runnerOnline: Boolean(heartbeat.runner_online),
      running: heartbeat.state === "running",
      runnerSeenAt: heartbeat.runner_seen_at,
      lastCompletedAt: heartbeat.last_completed_at,
      dueAt: heartbeat.due_at,
      cadenceSeconds: heartbeat.cadence_seconds ?? 21600,
    },
  };
}

async function refresh() {
  try {
    snapshot = normalize(await api("/api/system"));
  } catch {
    snapshot = null;
  }
}

const nodes = {
  pipelineUnit: document.getElementById("pipeline-unit"),
  pipelineState: document.getElementById("pipeline-state"),
  pipelineWork: document.getElementById("pipeline-work"),
  pipelineLast: document.getElementById("pipeline-last"),
  pipelineOutput: document.getElementById("pipeline-output"),
  schedulerUnit: document.getElementById("scheduler-unit"),
  schedulerState: document.getElementById("scheduler-state"),
  schedulerClock: document.getElementById("scheduler-clock"),
  schedulerCountdown: document.getElementById("scheduler-countdown"),
  schedulerCadence: document.getElementById("scheduler-cadence"),
  schedulerDue: document.getElementById("scheduler-due"),
  schedulerSeen: document.getElementById("scheduler-seen"),
  heartbeatUnit: document.getElementById("heartbeat-unit"),
  heartbeatState: document.getElementById("heartbeat-state"),
  heartbeatClock: document.getElementById("heartbeat-clock"),
  heartbeatCountdown: document.getElementById("heartbeat-countdown"),
  heartbeatCadence: document.getElementById("heartbeat-cadence"),
  heartbeatLast: document.getElementById("heartbeat-last"),
  heartbeatSeen: document.getElementById("heartbeat-seen"),
};

function timestamp(value) {
  const parsed = Date.parse(value ?? "");
  return Number.isNaN(parsed) ? null : parsed;
}

function ageMs(value, now) {
  const parsed = timestamp(value);
  return parsed == null ? Infinity : Math.max(0, now - parsed);
}

function fresh(value, now, thresholdMs) {
  return ageMs(value, now) <= thresholdMs;
}

function duration(milliseconds, { countdown = false } = {}) {
  if (!Number.isFinite(milliseconds)) return "—";
  const seconds = Math.max(0, Math.ceil(milliseconds / SECOND));
  if (seconds < 60) return countdown ? `${seconds}s` : `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return countdown ? `${minutes}m` : `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return countdown ? `${hours}h` : `${hours}h ago`;
}

function count(value) {
  return numberFormat.format(value ?? 0);
}

function createTicks() {
  const fragment = document.createDocumentFragment();
  for (let index = 0; index < 60; index += 1) {
    const tick = document.createElement("i");
    tick.style.setProperty("--tick", index);
    fragment.append(tick);
  }
  document.getElementById("scheduler-ticks").append(fragment);
}

function schedulerFacts(snapshot, now, observedAt) {
  const cadenceMs = snapshot.cadenceSeconds * SECOND;
  const freshnessMs = Math.max(75 * SECOND, cadenceMs * 1.5);
  const listening = snapshot.enabled
    && snapshot.runnerOnline
    && fresh(snapshot.runnerSeenAt, now, freshnessMs);
  const lastTick = timestamp(snapshot.lastTickAt);
  const runnerSeen = timestamp(snapshot.runnerSeenAt);
  const effectiveNow = listening
    ? now
    : !snapshot.enabled || !snapshot.runnerOnline
      ? observedAt
      : Math.min(now, (runnerSeen ?? observedAt) + freshnessMs);
  const elapsed = lastTick == null ? 0 : Math.max(0, effectiveNow - lastTick);
  const progress = listening ? (elapsed % cadenceMs) / cadenceMs : Math.min(1, elapsed / cadenceMs);
  const remaining = listening ? cadenceMs - (elapsed % cadenceMs) : 0;
  return { listening, progress, remaining };
}

function heartbeatFacts(snapshot, now, observedAt) {
  const freshnessMs = 3 * MINUTE;
  const listening = snapshot.configured
    && !snapshot.paused
    && snapshot.runnerOnline
    && fresh(snapshot.runnerSeenAt, now, freshnessMs);
  const last = timestamp(snapshot.lastCompletedAt);
  const due = timestamp(snapshot.dueAt);
  const total = last != null && due != null ? Math.max(1, due - last) : snapshot.cadenceSeconds * SECOND;
  const runnerSeen = timestamp(snapshot.runnerSeenAt);
  const effectiveNow = listening
    ? now
    : !snapshot.configured || snapshot.paused || !snapshot.runnerOnline
      ? observedAt
      : Math.min(now, (runnerSeen ?? observedAt) + freshnessMs);
  const elapsed = last == null ? 0 : Math.max(0, effectiveNow - last);
  const progress = Math.max(0, Math.min(1, elapsed / total));
  const remaining = due == null ? null : Math.max(0, due - now);
  return { listening, progress, remaining };
}

function renderPipeline(snapshot, now) {
  const live = snapshot.enabled
    && snapshot.runnerOnline
    && fresh(snapshot.runnerSeenAt, now, 75 * SECOND);
  const active = live && snapshot.cycleInFlight;
  nodes.pipelineUnit.dataset.live = String(live);
  nodes.pipelineUnit.dataset.active = String(active);
  nodes.pipelineUnit.dataset.tone = live ? "ok" : snapshot.enabled ? "warning" : "quiet";
  nodes.pipelineState.textContent = active ? "Live · processing" : live ? "Live · ready" : snapshot.enabled ? "Runner stale" : "Paused";
  nodes.pipelineWork.textContent = active
    ? `${count(snapshot.activeFeeds)} feeds in flight`
    : "No active cycle";
  nodes.pipelineLast.textContent = duration(ageMs(snapshot.lastWriteAt, now));
  nodes.pipelineOutput.textContent = `${count(snapshot.outputToday)} postings`;
}

function renderScheduler(snapshot, now) {
  const facts = schedulerFacts(snapshot, now, timestamp(windowSnapshot().observedAt) ?? now);
  nodes.schedulerUnit.dataset.live = String(facts.listening);
  nodes.schedulerUnit.dataset.tone = facts.listening ? "ok" : snapshot.enabled ? "warning" : "quiet";
  nodes.schedulerState.textContent = facts.listening ? "Live · listening" : snapshot.enabled ? "Runner stale" : "Paused";
  nodes.schedulerClock.style.setProperty("--progress", facts.progress);
  nodes.schedulerCountdown.textContent = facts.listening ? duration(facts.remaining, { countdown: true }) : "—";
  nodes.schedulerCadence.textContent = `${snapshot.cadenceSeconds}s`;
  nodes.schedulerDue.textContent = count(snapshot.dueFeeds);
  nodes.schedulerSeen.textContent = duration(ageMs(snapshot.runnerSeenAt, now));
}

function renderHeartbeat(snapshot, now) {
  const facts = heartbeatFacts(snapshot, now, timestamp(windowSnapshot().observedAt) ?? now);
  nodes.heartbeatUnit.dataset.live = String(facts.listening);
  nodes.heartbeatUnit.dataset.running = String(facts.listening && snapshot.running);
  nodes.heartbeatUnit.dataset.tone = facts.listening ? "ok" : snapshot.paused ? "quiet" : "warning";
  nodes.heartbeatState.textContent = snapshot.running
    ? "Live · validating"
    : facts.listening ? "Live · waiting" : snapshot.paused ? "Paused" : "Runner stale";
  nodes.heartbeatClock.style.setProperty("--progress", facts.progress);
  nodes.heartbeatCountdown.textContent = snapshot.paused || facts.remaining == null
    ? "—"
    : duration(facts.remaining, { countdown: true });
  nodes.heartbeatCadence.textContent = `${snapshot.cadenceSeconds / 3600}h`;
  nodes.heartbeatLast.textContent = duration(ageMs(snapshot.lastCompletedAt, now));
  nodes.heartbeatSeen.textContent = duration(ageMs(snapshot.runnerSeenAt, now));
}

let lastPaint = -Infinity;

function windowSnapshot() {
  return snapshot ?? { observedAt: new Date().toISOString() };
}

function frame(frameTime) {
  if (frameTime - lastPaint < 100) {
    requestAnimationFrame(frame);
    return;
  }
  lastPaint = frameTime;
  if (!snapshot) {
    requestAnimationFrame(frame);
    return;
  }
  const now = Date.now();
  renderPipeline(snapshot.pipeline, now);
  renderScheduler(snapshot.scheduler, now);
  renderHeartbeat(snapshot.heartbeat, now);
  requestAnimationFrame(frame);
}

createTicks();
refresh();
setInterval(refresh, 10_000);
requestAnimationFrame(frame);
