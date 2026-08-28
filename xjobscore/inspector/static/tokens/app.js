/* Tokens page: the discovery inventory, and enabling live ATS tokens into feed.
 *
 * `token` holds every board key ever seen; `feed` is the set the scheduler
 * polls. The default view is inactive live tokens; the status filter can also
 * show muted, dead, or all tokens (including actively polled live ones).
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el, formatCount, relativeTime } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";
import { heartbeatPresentation, tokenPromotable } from "/static/tokens/model.mjs";

// Show all tokens without pagination
const ROW_LIMIT = 5000;

// A vendor's tokens are validated a chunk at a time. The server probes three
// boards at once with a 10s timeout each, so a whole vendor in one request
// would outlive any browser or proxy patience; a chunk also gives the progress
// counter something real to count.
const VALIDATE_CHUNK = 20;

const state = {
  filter: "inactive",
  // Vendor to narrow to, client-side (the whole inventory is already on
  // screen -- see ROW_LIMIT). Empty string = all vendors.
  ats: "",
  total: 0,
  // Identity keys, kept across filter changes within an enable. Cleared
  // whenever the filters change, because the set on screen no longer
  // describes what is checked.
  selected: new Map(),
  // The one vendor group open, accordion-style; null = all closed. A single
  // name rather than a set of collapsed ones, so "only one at a time" is the
  // shape of the state instead of an invariant every writer must remember.
  expanded: null,
  rows: [],
  firstLoad: true,
  groupValidate: new Map(), // vendor -> {completed, total}
  validateAllRunning: false,
  heartbeat: null,
  // Newest-first probe log, and whether token_log could be read at all. An
  // unreadable log is not an empty one -- see the header dot.
  checks: [],
  logAvailable: false,
};

// The tail is short and the page is not a monitor: slower than the Scheduler's
// 5s, fast enough that a Validate-all shows up while you are watching it.
const LOG_REFRESH_MS = 10_000;
const LOG_LIMIT = 50;

const COLUMN_COUNT = 12;

const nodes = {
  filter: document.getElementById("status-feed-filter"),
  atsFilter: document.getElementById("ats-filter"),
  counts: document.getElementById("summary-line"),
  logLines: document.getElementById("token-log-lines"),
  logDot: document.getElementById("token-log-dot"),
  degraded: document.getElementById("degraded-banner"),
  error: document.getElementById("error-banner"),
  errorText: document.getElementById("error-text"),
  body: document.getElementById("tokens-body"),
  empty: document.getElementById("tokens-empty"),
  selectAll: document.getElementById("select-all"),
  selectionCount: document.getElementById("selection-count"),
  promote: document.getElementById("promote-btn"),
  heartbeatDot: document.getElementById("heartbeat-dot"),
  heartbeatState: document.getElementById("heartbeat-state"),
  heartbeatMeta: document.getElementById("heartbeat-meta"),
  heartbeatFacts: document.getElementById("heartbeat-facts"),
  heartbeatRun: document.getElementById("heartbeat-run"),
  heartbeatPause: document.getElementById("heartbeat-pause"),
  validateAll: document.getElementById("validate-all-btn"),
  range: document.getElementById("range"),
  truncated: document.getElementById("truncated-banner"),
};

const identity = (row) => `${row.ats} ${row.token}`;

const visibleRows = () => (state.ats ? state.rows.filter((row) => row.ats === state.ats) : state.rows);

renderNav("Tokens");

/* --- data ---------------------------------------------------------------- */

async function load() {
  const params = new URLSearchParams({
    filter: state.filter,
    limit: String(ROW_LIMIT),
    offset: "0",
  });
  try {
    const payload = await api(`/api/tokens?${params}`);
    showError(null);
    nodes.degraded.hidden = true;
    render(payload);
  } catch (error) {
    if (isDegraded(error)) {
      nodes.degraded.hidden = false;
      render({ rows: [], total: 0, counts: {}, ats_options: [] });
    } else {
      showError(error);
    }
  }
}

async function loadHeartbeat() {
  try {
    const previousCompletion = state.heartbeat?.last_completed_at;
    const payload = await api("/api/tokens/heartbeat");
    state.heartbeat = payload;
    renderHeartbeat();
    if (previousCompletion && payload.last_completed_at !== previousCompletion) {
      await load();
    }
  } catch (error) {
    state.heartbeat = {
      state: "offline",
      runner_online: false,
      paused: false,
      last_error: error.message ?? String(error),
    };
    renderHeartbeat();
  }
}

/* The counts header is served alongside the list, so re-ask for it with the
   smallest page the API will give rather than reloading the table underneath
   someone who is still working through it. */
async function refreshCounts() {
  const params = new URLSearchParams({ filter: state.filter, limit: "1", offset: "0" });
  try {
    const payload = await api(`/api/tokens?${params}`);
    renderCounts(payload.counts ?? {});
  } catch {
    // A stale header is not worth an error banner over a probe that worked.
  }
}

async function enableTokens() {
  const tokens = [...state.selected.values()].filter(t => {
    const row = state.rows.find(r => r.ats === t.ats && r.token === t.token);
    return row && tokenPromotable(row);
  });
  if (!tokens.length) return;
  nodes.promote.disabled = true;
  try {
    const result = await api("/api/tokens/enable", { body: { tokens } });
    showError(null);
    state.selected.clear();
    await load();
    await loadHeartbeat();
    note(
      `Promoted ${formatCount(result.imported)} selected tokens` +
        (result.skipped ? `; ${formatCount(result.skipped)} no longer eligible` : "")
    );
  } catch (error) {
    showError(error);
  } finally {
    syncPromote();
  }
}

/* Pause/resume writes `token_heartbeat.paused` in PostgreSQL, so it holds
   across restarts and applies to whichever process runs the loop -- not to
   this browser. The button therefore stays live whenever the database is
   reachable, even in an inspector whose own runner is off. */
async function toggleHeartbeatPause() {
  const paused = Boolean(state.heartbeat?.paused);
  nodes.heartbeatPause.disabled = true;
  try {
    state.heartbeat = await api("/api/tokens/heartbeat/control", {
      body: { paused: !paused },
    });
    showError(null);
    renderHeartbeat();
    note(paused ? "Token heartbeat resumed" : "Token heartbeat paused");
  } catch (error) {
    showError(error);
    await loadHeartbeat();
  }
}

async function runHeartbeat() {
  nodes.heartbeatRun.disabled = true;
  nodes.heartbeatRun.textContent = "Queueing…";
  try {
    state.heartbeat = await api("/api/tokens/heartbeat/run", { method: "POST" });
    showError(null);
    renderHeartbeat();
    note("Token heartbeat queued");
  } catch (error) {
    showError(error);
  } finally {
    await loadHeartbeat();
  }
}

async function validateToken(row) {
  const tokens = [{ ats: row.ats, token: row.token }];
  try {
    const result = await api("/api/tokens/validate", { body: { tokens } });
    if (result.results && result.results[0]) {
      const validation = result.results[0];
      Object.assign(row, { status: validation.status, checked_at: validation.checked_at });
      renderRows();
      syncPromote();
      await refreshCounts();
      await Promise.all([loadHeartbeat(), loadLog()]);
    }
  } catch (error) {
    showError(error);
  }
}

/* Validates exactly the rows on screen for this vendor -- the number on the
   button. The identities travel with the request rather than a bare vendor
   name, because the server has no idea which filter is in force and would
   otherwise probe every token the vendor has ever had. */
async function validateVendor(vendor, rows) {
  const identities = rows.map((row) => ({ ats: row.ats, token: row.token }));
  const progress = { completed: 0, total: identities.length };
  state.groupValidate.set(vendor, progress);
  renderRows();

  const byKey = new Map(state.rows.map((row) => [identity(row), row]));
  try {
    for (let i = 0; i < identities.length; i += VALIDATE_CHUNK) {
      const chunk = identities.slice(i, i + VALIDATE_CHUNK);
      const result = await api("/api/tokens/validate", { body: { tokens: chunk } });
      // Validation only records liveness. Muting a token is a deliberate,
      // separate action on the Feeds page -- a probe can be wrong (a
      // transient block, a markup change), so it must never delete on its own.
      for (const validation of result.results ?? []) {
        const row = byKey.get(`${validation.ats} ${validation.token}`);
        if (row) {
          Object.assign(row, {
            status: validation.status,
            checked_at: validation.checked_at,
          });
        }
      }
      progress.completed += chunk.length;
      renderRows();
    }
  } catch (error) {
    showError(error);
  } finally {
    state.groupValidate.delete(vendor);
    renderRows();
    syncPromote();
    // A vendor run writes a log row per token; do not make the reader wait a
    // full tick to see the batch it just started.
    await Promise.all([refreshCounts(), loadLog()]);
  }
}

/* --- render -------------------------------------------------------------- */

function render(payload) {
  state.total = payload.total ?? 0;
  renderCounts(payload.counts ?? {});
  state.rows = payload.rows ?? [];
  populateAtsFilter();
  renderRows();
  nodes.empty.hidden = state.rows.length > 0;
  renderRange();
  syncPromote();
}

function renderHeartbeat() {
  const heartbeat = state.heartbeat;
  if (!heartbeat) return;
  const presentation = heartbeatPresentation(heartbeat.state);
  nodes.heartbeatDot.dataset.state = presentation.tone;
  nodes.heartbeatState.textContent = presentation.label;

  const timing = [];
  if (heartbeat.paused && heartbeat.paused_changed_at) {
    timing.push(`Paused ${relativeTime(heartbeat.paused_changed_at)}`);
  }
  if (heartbeat.last_completed_at) {
    timing.push(`Last run ${relativeTime(heartbeat.last_completed_at)}`);
  }
  // A paused heartbeat has a due_at, but nothing will act on it -- promising
  // a next run would be a lie until someone resumes.
  if (heartbeat.due_at && heartbeat.state !== "running" && !heartbeat.paused) {
    timing.push(`Next run ${relativeTime(heartbeat.due_at)}`);
  }
  nodes.heartbeatMeta.textContent = timing.join(" · ");

  const failed = Boolean(heartbeat.last_error);
  nodes.heartbeatFacts.dataset.error = failed ? "true" : "false";
  nodes.heartbeatFacts.textContent = failed
    ? heartbeat.last_error
    : `${formatCount(heartbeat.eligible)} eligible · Last batch: ` +
      `${formatCount(heartbeat.last_validated)} checked · ` +
      `${formatCount(heartbeat.last_promoted)} promoted`;

  const paused = Boolean(heartbeat.paused);
  const connected = Boolean(heartbeat.postgres?.connected);
  const runnable = connected && heartbeat.runner_online && !paused
    && heartbeat.state !== "running";
  nodes.heartbeatRun.disabled = !runnable;
  nodes.heartbeatRun.textContent = heartbeat.state === "running" ? "Running…" : "Run now";
  nodes.heartbeatRun.title = paused
    ? "The token heartbeat is paused"
    : heartbeat.runner_online
      ? "Queue one token heartbeat batch"
      : "The token heartbeat runner is offline";

  nodes.heartbeatPause.disabled = !connected;
  nodes.heartbeatPause.textContent = paused ? "Resume" : "Pause";
  nodes.heartbeatPause.title = paused
    ? "Resume scheduled token validation and promotion"
    : "Stop scheduled token validation and promotion until resumed";
}

/* Grouped by vendor. Vendors are ordered alphabetically regardless of the
   row sort, so a group never splits across two positions in the table; rows
   keep whatever order the API returned. */
/* Rebuilds the vendor dropdown from whatever is loaded. Preserves the current
   selection unless the newly loaded set no longer has that vendor (e.g. the
   status filter changed underneath it), in which case it falls back to all. */
function populateAtsFilter() {
  const vendors = [...new Set(state.rows.map((row) => row.ats))].sort();
  const current = nodes.atsFilter.value;
  nodes.atsFilter.replaceChildren(
    el("option", { value: "" }, "All vendors"),
    ...vendors.map((vendor) => el("option", { value: vendor }, vendor))
  );
  if (vendors.includes(current)) {
    nodes.atsFilter.value = current;
  } else {
    state.ats = "";
    nodes.atsFilter.value = "";
  }
}

function renderRows() {
  const groups = new Map();
  for (const row of visibleRows()) {
    if (!groups.has(row.ats)) groups.set(row.ats, []);
    groups.get(row.ats).push(row);
  }
  const vendors = [...groups.keys()].sort((a, b) => groups.get(b).length - groups.get(a).length);
  // The biggest vendor opens itself on arrival, and again whenever the open
  // one is filtered off screen -- landing on a page of nothing but headers
  // reads as an empty inventory. A group the user closed by hand leaves
  // `expanded` null, which is left alone: that one was deliberate.
  if (state.firstLoad) {
    state.expanded = vendors[0] ?? null;
    state.firstLoad = false;
  } else if (state.expanded && !groups.has(state.expanded)) {
    state.expanded = vendors[0] ?? null;
  }
  const fragment = document.createDocumentFragment();
  for (const vendor of vendors) {
    const vendorRows = groups.get(vendor);
    const collapsed = state.expanded !== vendor;
    fragment.append(groupRow(vendor, vendorRows, collapsed));
    if (!collapsed) {
      for (const row of vendorRows) fragment.append(renderRow(row));
    }
  }
  nodes.body.replaceChildren(fragment);
}

function validateButton(row) {
  const button = el(
    "button",
    { class: "btn small", type: "button", title: "Check if this token is still live" },
    "Check"
  );
  if (state.validateAllRunning || state.groupValidate.size) button.disabled = true;
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    button.disabled = true;
    button.textContent = "…";
    try {
      await validateToken(row);
    } finally {
      button.disabled = false;
      button.textContent = "Check";
    }
  });
  return button;
}

function groupRow(vendor, rows, collapsed) {
  const progress = state.groupValidate.get(vendor);
  const validateLabel = progress
    ? `Validating ${progress.completed}/${progress.total}…`
    : `Validate ${formatCount(rows.length)}`;
  const validateButton = el(
    "button",
    {
      class: "btn small",
      type: "button",
      onclick: (event) => {
        event.stopPropagation();
        validateVendor(vendor, rows);
      },
      title: `Validate all ${rows.length} tokens in this vendor`,
    },
    validateLabel
  );
  if (progress || state.validateAllRunning) {
    validateButton.disabled = true;
  }

  const row = el(
    "tr",
    { class: "group-row" },
    el(
      "td",
      { colspan: COLUMN_COUNT },
      el(
        "span",
        { style: "display:flex;align-items:center;gap:12px" },
        validateButton,
        el("span", {}, `${collapsed ? "▸" : "▾"} ${vendor}`),
        el("span", { class: "secondary" }, `(${formatCount(rows.length)})`)
      )
    )
  );
  row.addEventListener("click", (e) => {
    if (e.target !== validateButton) {
      // Opening one closes whichever was open; clicking the open one closes it.
      state.expanded = collapsed ? vendor : null;
      renderRows();
      syncPromote();
    }
  });
  return row;
}

function renderCounts(counts) {
  if (counts.total == null) {
    nodes.counts.textContent = "";
    return;
  }
  // Status is only half the shape of an inventory; the freshness segments say
  // how much of it is a current answer rather than a remembered one.
  const parts = [
    `${formatCount(counts.total)} tokens / ${formatCount(counts.vendors)} vendors`,
    el("span", { class: "tok-sum-ok" }, `${formatCount(counts.live)} live`),
    el("span", { class: counts.dead ? "tok-sum-bad" : null }, `${formatCount(counts.dead)} dead`),
    `${formatCount(counts.muted)} muted`,
    `${formatCount(counts.promotable)} live and unpromoted`,
    `${formatCount(counts.checked_24h)} checked today`,
    el(
      "span",
      { class: counts.never_checked ? "tok-sum-bad" : null },
      `${formatCount(counts.never_checked)} never checked`
    ),
  ];
  nodes.counts.replaceChildren(
    ...parts
      .flatMap((part, index) => [
        index ? el("span", { class: "tok-sum-sep", "aria-hidden": "true" }, "·") : null,
        typeof part === "string" ? el("span", {}, part) : part,
      ])
      .filter(Boolean)
  );
}

/* The tail log reads `token_log`, one row per probe. Rendered oldest-at-top so
   new lines arrive at the bottom, and the scroll only follows when the reader
   is already parked there -- scrolling back through history should stay put. */
function renderLog() {
  nodes.logDot.dataset.state = state.logAvailable ? "live" : "off";
  if (!state.checks.length) {
    nodes.logLines.replaceChildren(
      el(
        "div",
        { class: "tok-log-empty" },
        state.logAvailable ? "no checks recorded yet…" : "check log unavailable"
      )
    );
    return;
  }
  const pinned =
    nodes.logLines.scrollHeight - nodes.logLines.scrollTop - nodes.logLines.clientHeight < 12;
  nodes.logLines.replaceChildren(
    ...[...state.checks].reverse().map((check) =>
      el(
        "div",
        { class: "tok-log-line" },
        el("time", { title: check.checked_at ?? "" }, (check.checked_at ?? "").slice(11, 19)),
        el("span", { class: "tok-log-st", "data-status": check.status }, check.status),
        el("span", { class: "tok-log-token", title: `${check.ats}:${check.token}` }, check.token),
        el("span", { class: "tok-log-vendor" }, check.ats)
      )
    )
  );
  if (pinned) nodes.logLines.scrollTop = nodes.logLines.scrollHeight;
}

async function loadLog() {
  try {
    const view = await api(`/api/tokens/log?limit=${LOG_LIMIT}`);
    state.logAvailable = Boolean(view.log_available);
    state.checks = view.checks ?? [];
  } catch {
    state.logAvailable = false;
  }
  renderLog();
}

async function saveNote(row, input) {
  const previous = row.note ?? "";
  const next = input.value.trim() || null;
  if (next === (row.note ?? null)) return;

  input.disabled = true;
  input.classList.add("saving");
  try {
    const saved = await api("/api/tokens/note", {
      method: "PUT",
      body: { ats: row.ats, token: row.token, note: next },
    });
    row.note = saved.note;
    input.value = saved.note ?? "";
  } catch (error) {
    input.value = previous;
    showError(error);
  } finally {
    input.disabled = false;
    input.classList.remove("saving");
  }
}

async function setMuted(row, input) {
  const previous = Boolean(row.muted);
  input.disabled = true;
  try {
    const saved = await api("/api/tokens/muted", {
      method: "PUT",
      body: { ats: row.ats, token: row.token, muted: input.checked },
    });
    row.muted = saved.muted;
    if (saved.feed_dropped) row.feed_state = "none";
    state.selected.delete(identity(row));
    await Promise.all([load(), loadHeartbeat()]);
    note(saved.muted ? "Token muted" : "Token unmuted");
  } catch (error) {
    input.checked = previous;
    showError(error);
  } finally {
    input.disabled = false;
    syncPromote();
  }
}

function renderRow(row) {
  const key = identity(row);
  const check = el("input", {
    type: "checkbox",
    disabled: !tokenPromotable(row),
    onchange: (event) => {
      if (event.target.checked) {
        state.selected.set(key, { ats: row.ats, token: row.token });
      } else {
        state.selected.delete(key);
      }
      syncPromote();
    },
  });
  check.checked = state.selected.has(key);

  const mutedInput = el("input", {
    type: "checkbox",
    checked: Boolean(row.muted),
    "aria-label": `Mute ${row.ats} ${row.token}`,
    onchange: (event) => setMuted(row, event.target),
  });

  const actionTd = el("td", {});
  actionTd.append(validateButton(row));

  const noteInput = el("input", {
    class: "tok-note-input",
    type: "text",
    value: row.note ?? "",
    placeholder: "Add note…",
    title: "Saves when focus leaves this field",
    "aria-label": `Note for ${row.ats} ${row.token}`,
  });
  noteInput.addEventListener("change", () => saveNote(row, noteInput));
  noteInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      noteInput.blur();
    }
  });

  const company = el(
    "span",
    { class: "tok-company" },
    row.company ?? el("span", { class: "muted" }, "—"),
    ...(row.tags ?? []).map(tagBadge)
  );

  return el(
    "tr",
    {},
    el("td", { class: "tok-check" }, check),
    actionTd,
    el("td", { class: "mono" }, row.ats),
    el("td", { class: "mono" }, row.token),
    el("td", {}, company),
    el("td", {}, chip(row.status)),
    el("td", {}, chip(row.feed_state)),
    el("td", {}, el("label", { class: "tok-muted-toggle" }, mutedInput, row.muted ? "Muted" : "")),
    el("td", { class: "muted" }, row.source ?? "registry"),
    el("td", { class: "tok-note-cell" }, noteInput),
    el("td", { class: "secondary" }, row.discovered_at ? relativeTime(row.discovered_at) : "—"),
    el("td", { class: "secondary" }, row.checked_at ? relativeTime(row.checked_at) : "—")
  );
}

function tagBadge(tag) {
  return el(
    "span",
    {
      class: "tok-tag",
      "data-tag": tag,
      title: `${tag.toUpperCase()} token`,
    },
    tag.toLowerCase()
  );
}

/* Namespaced rather than reusing .chip[data-status], whose vocabulary is the
   poll outcomes (ok / partial / error) and means something else entirely. */
function chip(value) {
  return el(
    "span",
    { class: "chip tok-chip", "data-state": value },
    el("span", { class: "dot" }),
    value === "none" ? "not in feed" : value
  );
}

function renderRange() {
  const shown = visibleRows().length;
  // Truncation is about what the server handed over (state.rows vs.
  // state.total), independent of the vendor dropdown narrowing it further.
  const truncated = state.rows.length < state.total;
  nodes.range.textContent = state.ats
    ? `${formatCount(shown)} matching tokens for ${state.ats}`
    : truncated
      ? `Showing ${formatCount(shown)} of ${formatCount(state.total)} matching tokens`
      : `${formatCount(shown)} matching tokens`;
  nodes.truncated.hidden = !truncated;
  if (truncated) {
    nodes.truncated.textContent =
      `Only the first ${formatCount(state.rows.length)} of ${formatCount(state.total)} matching tokens are shown — narrow the filters above to see the rest.`;
  }
}

function syncPromote() {
  const liveSelected = [...state.selected.values()].filter(t => {
    const row = state.rows.find(r => r.ats === t.ats && r.token === t.token);
    return row && tokenPromotable(row);
  });
  const count = liveSelected.length;
  nodes.promote.disabled = count === 0;
  nodes.promote.textContent = count
    ? `Promote ${formatCount(count)} selected`
    : "Promote selected";
  nodes.selectionCount.textContent = state.selected.size ? `${formatCount(state.selected.size)} selected (${formatCount(count)} promotable)` : "";
  // Based on all selectable rows in view, not just the ones currently
  // rendered -- collapsed vendor groups have no checkboxes in the DOM at all.
  const selectable = visibleRows().filter(tokenPromotable);
  const allSelected = selectable.length > 0 && selectable.every((row) => state.selected.has(identity(row)));
  nodes.selectAll.checked = allSelected;
  nodes.selectAll.indeterminate = !allSelected && selectable.some((row) => state.selected.has(identity(row)));
}

function showError(error) {
  nodes.error.hidden = !error;
  nodes.error.classList.add("error");
  if (error) nodes.errorText.textContent = error.message ?? String(error);
}

/* Same banner, without the error styling -- one place for the page to talk. */
let noteTimer = null;
function note(message) {
  nodes.errorText.textContent = message;
  nodes.error.classList.remove("error");
  nodes.error.hidden = false;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => {
    nodes.error.hidden = true;
    nodes.error.classList.add("error");
  }, 4000);
}

/* --- wiring -------------------------------------------------------------- */

nodes.selectAll.addEventListener("change", (event) => {
  const checked = event.target.checked;
  for (const row of visibleRows()) {
    if (!tokenPromotable(row)) continue;
    const key = identity(row);
    if (checked) state.selected.set(key, { ats: row.ats, token: row.token });
    else state.selected.delete(key);
  }
  renderRows();
  syncPromote();
});

nodes.atsFilter.addEventListener("change", (event) => {
  state.ats = event.target.value;
  renderRows();
  renderRange();
  syncPromote();
});

nodes.filter.addEventListener("change", (event) => {
  state.filter = event.target.value;
  state.firstLoad = true;
  load();
});

nodes.promote.addEventListener("click", enableTokens);
nodes.heartbeatRun.addEventListener("click", runHeartbeat);
nodes.heartbeatPause.addEventListener("click", toggleHeartbeatPause);

nodes.validateAll.addEventListener("click", async () => {
  if (!state.rows.length) return;
  state.validateAllRunning = true;
  nodes.validateAll.disabled = true;
  const original = nodes.validateAll.textContent;
  try {
    const vendorGroups = new Map();
    for (const row of state.rows) {
      if (!vendorGroups.has(row.ats)) vendorGroups.set(row.ats, []);
      vendorGroups.get(row.ats).push(row);
    }
    for (const [vendor, vendorRows] of vendorGroups) {
      nodes.validateAll.textContent = `Validating ${vendor}…`;
      await validateVendor(vendor, vendorRows);
    }
  } finally {
    state.validateAllRunning = false;
    nodes.validateAll.disabled = false;
    nodes.validateAll.textContent = original;
  }
});

load();
loadHeartbeat();
loadLog();
setInterval(loadHeartbeat, 30_000);
setInterval(loadLog, LOG_REFRESH_MS);
