/* The Activity pane (Enrich, above the board) and the Activity log (below it)
   combine process logs, durable polls, and system runs. Scheduler controls
   and workers remain owned by board.js and live.js. */
import { el, formatCount } from "/static/shared/format.js";
import {
  activityEntries, enrichmentSummary, filterActivity, nextCheck,
} from "/static/system/activity-model.mjs";

const $ = (id) => document.getElementById(id);
const pane = $("activity-pane");
const logPane = $("activity-log");
const state = {
  run: null, worker: null, logs: [], polls: [], runs: [], boot: "",
  historyAvailable: false, logsAvailable: false, pollsAvailable: false,
  view: "all", following: true, expanded: new Set(), enrichmentSignature: null,
};

function fullTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString([], {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23", timeZoneName: "short",
  });
}

function clockTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
}

function paintNextCheck() {
  if (!pane) return;
  const check = nextCheck(state.worker);
  $("next-check").textContent = check.text;
  $("next-check").title = check.title;
}

function paintEnrichment() {
  if (!pane) return;
  const signature = JSON.stringify([state.run, state.historyAvailable, state.worker?.last_error]);
  if (signature === state.enrichmentSignature) return;
  state.enrichmentSignature = signature;
  const summary = enrichmentSummary(state.run);
  const errors = [...summary.errors];
  if (state.worker?.last_error) errors.push({ label: "Worker", detail: state.worker.last_error });
  $("enrich-issues").dataset.state = errors.length ? "failed" : "";
  $("enrich-issue-count").textContent = state.historyAvailable ? String(errors.length) : "—";
  $("enrich-history-note").hidden = state.historyAvailable;
  $("enrich-history-note").textContent = "History unavailable";
  $("enrich-entry").hidden = !state.run;
  $("enrich-empty").hidden = Boolean(state.run);
  $("enrich-empty").textContent = state.historyAvailable ? "No completed enrichment run yet." : "Enrichment history unavailable.";
  if (state.run) {
    const stamp = $("enrich-completed-at");
    stamp.dateTime = state.run.finished_at;
    stamp.textContent = fullTime(state.run.finished_at);
    stamp.title = `Last completed: ${stamp.textContent}`;
    $("enrich-outcome").textContent = summary.outcome;
    $("enrich-outcome").dataset.state = summary.tone;
    $("enrich-postings").textContent = summary.postings == null ? "Postings count unavailable"
      : `${formatCount(summary.postings)} postings enriched`;
    $("enrich-duration").textContent = summary.duration;
    $("enrich-timing-rows").replaceChildren(...summary.timings.map((step) =>
      el("span", { class: "enrich-timing", "data-state": step.failed ? "failed" : "complete" },
        el("span", {}, `${step.label}${step.failed ? " (failed)" : ""}`), el("span", {}, step.elapsed))));
  }
  $("enrich-alert").hidden = !errors.length;
  $("enrich-alert-message").textContent = errors.length
    ? errors.map((error) => `${error.label}: ${error.detail.split("\n")[0]}`).join("; ") : "";
  $("enrich-error-trace").textContent = errors.map((error) => `${error.label}\n${error.detail}`).join("\n\n");
  $("enrich-error-toggle").setAttribute("aria-expanded", "false");
  $("enrich-error-details").hidden = true;
}

export function setEnrichmentWorker(worker) {
  state.worker = worker;
  paintNextCheck();
  paintEnrichment();
}

export function setRunActivity(runs, run, available) {
  const changed = JSON.stringify([state.runs, state.historyAvailable]) !== JSON.stringify([runs, available]);
  state.runs = runs;
  state.run = run;
  state.historyAvailable = available;
  paintEnrichment();
  if (changed) renderLogs();
}

export function setServerActivity(logs, available, boot) {
  const changed = JSON.stringify([state.logs, state.logsAvailable, state.boot]) !== JSON.stringify([logs, available, boot]);
  state.logs = logs;
  state.logsAvailable = available;
  state.boot = boot;
  if (changed) renderLogs();
}

export function setPollActivity(polls, available) {
  const changed = JSON.stringify([state.polls, state.pollsAvailable]) !== JSON.stringify([polls, available]);
  state.polls = polls;
  state.pollsAvailable = available;
  if (changed) renderLogs();
}

function updateFollow() {
  if (!logPane) return;
  $("activity-follow").setAttribute("aria-pressed", String(state.following));
  $("activity-follow").textContent = state.following ? "↓ following" : "↓ follow latest";
}

let renderSignature = null;
function renderLogs() {
  if (!logPane) return;
  const entries = activityEntries({ logs: state.logs, polls: state.polls, runs: state.runs, boot: state.boot });
  const processSelect = $("activity-process");
  const selected = processSelect.value;
  const processes = [...new Set(entries.map((entry) => entry.process))].sort();
  if (selected !== "all" && !processes.includes(selected)) processes.push(selected);
  const options = [el("option", { value: "all" }, "all processes"),
    ...processes.map((process) => el("option", { value: process }, process))];
  const optionSignature = processes.join("\n");
  if (processSelect.dataset.options !== optionSignature) {
    processSelect.replaceChildren(...options);
    processSelect.value = selected || "all";
    processSelect.dataset.options = optionSignature;
  }
  const filtered = filterActivity(entries, {
    view: state.view, process: processSelect.value,
    query: $("activity-search").value, allPolls: $("activity-all-polls").checked,
  });
  $("activity-all-count").textContent = filtered.total;
  $("activity-issue-count").textContent = filtered.issues;
  const available = state.logsAvailable || state.pollsAvailable || state.historyAvailable;
  const missing = [!state.logsAvailable && "server logs", !state.pollsAvailable && "poll history",
    !state.historyAvailable && "system history"].filter(Boolean);
  $("activity-shown").textContent = available
    ? `${formatCount(filtered.entries.length)} events${filtered.hidden ? `; ${formatCount(filtered.hidden)} routine polls hidden` : ""}`
    : "Activity unavailable";
  $("activity-availability").textContent = missing.length ? `${missing.join(", ")} unavailable` : "";
  const signature = JSON.stringify([filtered.entries, available]);
  if (signature === renderSignature) return;
  renderSignature = signature;
  const stream = $("activity-stream");
  const scrollTop = stream.scrollTop;
  const focusedId = document.activeElement?.dataset.activityEntry;
  const rendered = filtered.entries.map((entry) => {
    const level = el("span", { class: "activity-level" }, entry.level);
    const wrapper = el("div", { class: "activity-entry", "data-level": entry.level },
      el("div", { class: "activity-row" },
        el("time", { datetime: entry.ts, title: fullTime(entry.ts) }, clockTime(entry.ts)), level,
        el("span", { class: "activity-process", title: entry.logger ?? entry.process }, entry.process),
        el("span", { class: "activity-message", title: entry.msg }, entry.msg)));
    if (entry.detail) {
      const id = `activity-trace-${encodeURIComponent(entry.id)}`;
      const expanded = state.expanded.has(entry.id);
      const pre = el("pre", { id, class: "activity-trace", hidden: !expanded }, entry.detail);
      const toggle = el("button", {
        class: "activity-trace-toggle", type: "button", "aria-controls": id,
        "aria-expanded": String(expanded), "aria-label": `Error details for ${entry.process}`,
        "data-activity-entry": entry.id,
        onclick: () => {
          pre.hidden = !pre.hidden;
          toggle.textContent = pre.hidden ? "▸" : "▾";
          toggle.setAttribute("aria-expanded", String(!pre.hidden));
          if (pre.hidden) state.expanded.delete(entry.id); else state.expanded.add(entry.id);
        },
      }, expanded ? "▾" : "▸");
      level.append(toggle);
      wrapper.append(pre);
    }
    return wrapper;
  });
  stream.replaceChildren(...(rendered.length ? rendered : [el("div", { class: "activity-empty" },
    available ? entries.length ? "No matching activity." : "No activity recorded yet." : "Activity unavailable.")]));
  if (focusedId) {
    [...stream.querySelectorAll("button[data-activity-entry]")]
      .find((button) => button.dataset.activityEntry === focusedId)?.focus({ preventScroll: true });
  }
  stream.scrollTop = state.following ? stream.scrollHeight : scrollTop;
}

if (pane && logPane) {
  $("enrich-error-toggle").addEventListener("click", () => {
    const button = $("enrich-error-toggle");
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(expanded));
    $("enrich-error-details").hidden = !expanded;
  });
  const durationWrap = $("enrich-duration-wrap");
  durationWrap.addEventListener("keydown", (event) => {
    if (event.key === "Escape") durationWrap.classList.add("tooltip-dismissed");
  });
  durationWrap.addEventListener("mouseleave", () => durationWrap.classList.remove("tooltip-dismissed"));
  durationWrap.addEventListener("focusout", () => durationWrap.classList.remove("tooltip-dismissed"));
  $("enrich-duration-trigger").addEventListener("click", () => durationWrap.classList.remove("tooltip-dismissed"));
  for (const button of logPane.querySelectorAll("[data-activity-view]")) {
    button.addEventListener("click", () => {
      state.view = button.dataset.activityView;
      for (const item of logPane.querySelectorAll("[data-activity-view]")) {
        item.setAttribute("aria-pressed", String(item === button));
      }
      renderLogs();
    });
  }
  for (const id of ["activity-process", "activity-all-polls"]) $(id).addEventListener("change", renderLogs);
  $("activity-search").addEventListener("input", renderLogs);
  $("activity-follow").addEventListener("click", () => {
    state.following = !state.following;
    if (state.following) $("activity-stream").scrollTop = $("activity-stream").scrollHeight;
    updateFollow();
  });
  $("activity-stream").addEventListener("scroll", () => {
    const stream = $("activity-stream");
    state.following = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 12;
    updateFollow();
  });
  paintNextCheck();
  paintEnrichment();
  renderLogs();
  setInterval(() => { if (!document.hidden) paintNextCheck(); }, 1000);
}
