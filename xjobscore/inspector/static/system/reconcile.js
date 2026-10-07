import { api } from "/static/shared/api.js";

const panel = document.getElementById("reconcile-panel");
const element = name => document.getElementById(`reconcile-${name}`);
const form = element("settings");
const fields = ["concurrency", "checks_per_second", "recheck_minutes", "retry_minutes"];
const endpoint = "/api/reconcile/control";
let current = null;
let readError = null;
let actionError = null;
let saveMessage = "";
let busy = null;
let updatedAt = null;
let revision = 0;
let loading = null;
let loadController = null;

function settings() {
  const control = current.control;
  return { concurrency: control.concurrency, checks_per_second: control.checks_per_second,
    recheck_minutes: control.recheck_s / 60, retry_minutes: control.retry_s / 60 };
}

function hasChanges() {
  if (!current) return false;
  const saved = settings();
  return fields.some(name => form.elements[name].value === "" || Number(form.elements[name].value) !== saved[name]);
}

function fillSettings() {
  const saved = settings();
  for (const name of fields) form.elements[name].value = saved[name];
}

function receive(view, resetDraft = false) {
  if (!view?.control || !view?.queue || !view?.worker) throw new Error("Invalid link-check response.");
  const keepDraft = hasChanges();
  current = view;
  readError = null;
  updatedAt = Date.now();
  if (resetDraft || !keepDraft) fillSettings();
}

function count(value) {
  return Number.isFinite(value) ? value.toLocaleString() : "—";
}

function age(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
  return `${Math.floor(hours / 24)}d${hours % 24 ? ` ${hours % 24}h` : ""}`;
}

function paint() {
  if (!panel) return;
  const queue = current?.queue;
  const control = current?.control;
  const worker = current?.worker;
  let state = "idle";
  let label = readError ? "Unavailable" : "Loading";
  if (current) {
    if (worker.last_error) { state = "error"; label = "Worker error"; }
    else if (!worker.alive) { state = "offline"; label = "Offline"; }
    else if (!control.running) { state = "paused"; label = "Paused"; }
    else if (!queue.ready && !queue.checking) { label = "Idle"; }
    else { state = "running"; label = "Running"; }
  } else if (readError) state = "offline";
  element("status").textContent = label;
  element("status").dataset.state = state;
  element("ready").textContent = count(queue?.ready);
  element("checking").textContent = count(queue?.checking);
  element("capacity").textContent = current ? `/${count(control.concurrency)}` : "";
  element("scheduled").textContent = count(queue?.waiting);
  const oldest = queue?.oldest_ready_at ? Date.parse(queue.oldest_ready_at) : NaN;
  element("oldest").textContent = Number.isFinite(oldest) ? age(Date.now() - oldest) : "—";
  element("summary").dataset.stale = String(Boolean(readError && current));

  const disabled = !current || Boolean(readError || busy);
  element("toggle").disabled = disabled;
  element("toggle-label").textContent = busy === "toggle" ? "Saving…" : control?.running === false ? "Resume" : "Pause";
  element("toggle").title = control?.running === false ? "Resume new checks." : "Pause new checks; current checks finish.";
  element("toggle").querySelector("path").setAttribute("d", control?.running === false ? "M2 1l7 4-7 4z" : "M2 1h2v8H2zM6 1h2v8H6z");
  element("fields").disabled = disabled;
  element("save").disabled = disabled || !hasChanges();
  element("save").textContent = busy === "settings" ? "Saving…" : "Save changes";
  element("save-note").textContent = actionError?.kind === "settings" ? actionError.message : saveMessage || (hasChanges() ? "Unsaved changes" : "");
  element("save-note").dataset.error = String(actionError?.kind === "settings");
  element("details-toggle").disabled = !current;
  element("completed").textContent = count(queue?.completed);
  element("cancelled").textContent = count(queue?.cancelled);
  element("recorded-error").hidden = !queue?.last_error;
  element("last-error").textContent = queue?.last_error || "";

  let notice = "";
  let tone = "warning";
  if (readError) {
    notice = current ? "Couldn’t refresh. Showing the last known queue." : "Couldn’t load link checks.";
    tone = "error";
  } else if (actionError?.kind === "toggle") {
    notice = actionError.message;
    tone = "error";
  } else if (worker?.last_error) {
    notice = `Worker error: ${worker.last_error}`;
    tone = "error";
  } else if (current && !worker.alive) {
    notice = "Worker offline. Queued checks are retained.";
  } else if (current && !control.running && queue.checking > 0) {
    notice = `Paused. The ${count(queue.checking)} in-progress ${queue.checking === 1 ? "check will" : "checks will"} finish.`;
  }
  element("notice").hidden = !notice;
  element("notice").dataset.tone = tone;
  element("notice").title = readError?.message || "";
  element("notice-text").textContent = notice;
  element("retry").hidden = !readError;
  element("retry").disabled = Boolean(loading);
  element("updated").textContent = current
    ? readError ? `Stale · updated ${age(Date.now() - updatedAt)} ago` : "Updated just now"
    : readError ? "Update unavailable" : "Reading queue…";
}

async function request(options, controller) {
  const timeout = setTimeout(() => controller.abort(), 15_000);
  try {
    return await api(endpoint, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Link-check request timed out.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

// The System page calls this on its existing 30-second overview refresh.
export async function loadReconcile() {
  if (!panel || busy) return;
  if (loading) return loading;
  const version = ++revision;
  const controller = new AbortController();
  loadController = controller;
  loading = (async () => {
    try {
      const view = await request({}, controller);
      if (version === revision) receive(view);
    } catch (error) {
      if (version === revision) readError = error;
    } finally {
      loading = null;
      if (loadController === controller) loadController = null;
      paint();
    }
  })();
  paint();
  return loading;
}

async function save(body, kind) {
  if (!current || busy || readError) return;
  // A refresh started before this write must not overwrite its response.
  revision++;
  loadController?.abort();
  busy = kind;
  actionError = null;
  saveMessage = "";
  paint();
  try {
    const view = await request({ method: "POST", body }, new AbortController());
    receive(view, kind === "settings");
    if (kind === "settings") saveMessage = "Saved";
  } catch (error) {
    actionError = { kind, message: kind === "settings"
      ? `Couldn’t save. Your changes are still here. ${error.message}`
      : `Couldn’t ${current.control.running ? "pause" : "resume"} checks. ${error.message}` };
  } finally {
    busy = null;
    paint();
  }
}

function disclose(name, open) {
  element(`${name}-toggle`).setAttribute("aria-expanded", String(open));
  element(name === "settings" ? "settings-panel" : "details").hidden = !open;
}

element("toggle")?.addEventListener("click", () => current && save({ running: !current.control.running }, "toggle"));
element("retry")?.addEventListener("click", loadReconcile);
for (const name of ["settings", "details"]) {
  element(`${name}-toggle`)?.addEventListener("click", () => {
    disclose(name, element(`${name}-toggle`).getAttribute("aria-expanded") !== "true");
  });
}
form?.addEventListener("input", () => {
  if (actionError?.kind === "settings") actionError = null;
  saveMessage = "";
  paint();
});
element("cancel")?.addEventListener("click", () => {
  if (current) fillSettings();
  if (actionError?.kind === "settings") actionError = null;
  saveMessage = "";
  disclose("settings", false);
  paint();
  element("settings-toggle").focus();
});
form?.addEventListener("submit", event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  save({ concurrency: Number(form.elements.concurrency.value),
    checks_per_second: Number(form.elements.checks_per_second.value),
    recheck_s: Math.round(Number(form.elements.recheck_minutes.value) * 60),
    retry_s: Math.round(Number(form.elements.retry_minutes.value) * 60) }, "settings");
});
