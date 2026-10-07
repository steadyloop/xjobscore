/* Tokens page: the discovery inventory, and enabling live ATS tokens into feed.
 *
 * `token` holds every board key ever seen; `feed` is the set the scheduler
 * polls. The default view is inactive live tokens; the status filter can also
 * show muted, dead, or all tokens (including actively polled live ones).
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el, formatCount, relativeTime } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";
import {
  clock,
  compactCount,
  heartbeatPresentation,
  shortDuration,
  sweepActive,
  sweepHeadline,
  sweepRate,
  sweepVendorRows,
  tokenPromotable,
} from "/static/tokens/model.mjs";

// An open vendor group shows one page of rows at a time, fetched from the
// server. Group headers come from per-vendor counts, so every vendor shows no
// matter how large the filter is, and the DOM never holds more than a page.
const PAGE_SIZE = 100;
// Typing in the search box waits this long before asking the server.
const SEARCH_DEBOUNCE_MS = 300;

// Adding at least this many at once asks first: each one becomes a feed the
// scheduler polls from then on.
const CONFIRM_ADD_AT = 100;

const state = {
  filter: "inactive",
  // Vendor to narrow to (group headers are already loaded). "" = all vendors.
  ats: "",
  // Server-side search over token and company, and the row order in a group.
  q: "",
  order: "token",
  total: 0,
  // Identity key -> {ats, token, promotable}. Kept across pages, so a
  // selection can span several; `promotable` is captured at check time
  // because the row itself may be on a page no longer loaded.
  selected: new Map(),
  // Whole vendors ticked for "Add to feed". The server resolves each one under
  // the filter and search in force, so no vendor's rows need to be loaded.
  selectedGroups: new Set(),
  // The one vendor group open, accordion-style; null = all closed, which is
  // also how the page starts. A single name rather than a set of collapsed
  // ones, so "only one at a time" is the shape of the state.
  expanded: null,
  // The open group's current page of rows, and where that page starts.
  rows: [],
  offset: 0,
  // Per-vendor sizes under the current filter: the group headers.
  groups: [],
  // True while the open group's page is in flight.
  pageLoading: false,
  // Bumped by every load() and page fetch; a response that lands after the
  // view moved on belongs to the old view and is dropped.
  generation: 0,
  heartbeat: null,
  // Newest-first probe log, and whether token_log could be read at all. An
  // unreadable log is not an empty one -- see the header dot.
  checks: [],
  logAvailable: false,
  // The same table's other verb: feed arrivals and departures. Tracked apart
  // from `checks` so one panel can be empty while the other is full -- a quiet
  // fleet checks constantly and enters nothing.
  feedEvents: [],
  feedLogAvailable: false,
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
  feedLogLines: document.getElementById("feed-log-lines"),
  feedLogDot: document.getElementById("feed-log-dot"),
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
  addInterval: document.getElementById("add-interval"),
  range: document.getElementById("range"),
  order: document.getElementById("order-filter"),
  search: document.getElementById("search"),
};

const identity = (row) => `${row.ats} ${row.token}`;

const visibleRows = () => state.rows;
const visibleGroups = () => (state.ats ? state.groups.filter((group) => group.ats === state.ats) : state.groups);
const groupCount = (vendor) => state.groups.find((group) => group.ats === vendor)?.count ?? 0;

/* The filter, search and order every token request shares. */
function viewParams(extra) {
  const params = new URLSearchParams({ filter: state.filter, order: state.order, ...extra });
  if (state.q) params.set("q", state.q);
  return params;
}

renderNav("Tokens");

/* --- data ---------------------------------------------------------------- */

async function load() {
  const generation = ++state.generation;
  const params = viewParams({ limit: "1", offset: "0", groups: "true" });
  try {
    const payload = await api(`/api/tokens?${params}`);
    if (generation !== state.generation) return;
    showError(null);
    nodes.degraded.hidden = true;
    state.groups = payload.groups ?? [];
    // An open group stays open on its page, unless the view no longer has it.
    if (state.expanded && !groupCount(state.expanded)) state.expanded = null;
    state.rows = [];
    render(payload);
    if (state.expanded) loadPage(state.expanded, state.offset);
  } catch (error) {
    if (isDegraded(error)) {
      nodes.degraded.hidden = false;
      state.rows = [];
      state.expanded = null;
      render({ groups: [], total: 0, counts: {} });
    } else {
      showError(error);
    }
  }
}

/* One page of the open vendor group, from the server. */
async function loadPage(vendor, offset) {
  const generation = ++state.generation;
  const total = groupCount(vendor);
  // Clamp: a page past the end (the group shrank) falls back to its last page.
  offset = Math.max(0, Math.min(offset, Math.max(0, total - 1)));
  offset -= offset % PAGE_SIZE;
  state.pageLoading = true;
  renderRows();
  try {
    const params = viewParams({ ats: vendor, limit: String(PAGE_SIZE), offset: String(offset) });
    const payload = await api(`/api/tokens?${params}`);
    if (generation !== state.generation || state.expanded !== vendor) return;
    state.rows = payload.rows ?? [];
    state.offset = offset;
  } catch (error) {
    if (generation === state.generation) showError(error);
  } finally {
    if (generation === state.generation) {
      state.pageLoading = false;
      renderRows();
      syncPromote();
    }
  }
}

function openGroup(vendor) {
  state.expanded = vendor;
  state.rows = [];
  state.offset = 0;
  if (vendor) loadPage(vendor, 0);
  else renderRows();
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

async function refreshCounts() {
  const params = new URLSearchParams({ filter: state.filter, limit: "1", offset: "0" });
  try {
    const payload = await api(`/api/tokens?${params}`);
    renderCounts(payload.counts ?? {});
  } catch {
    // A stale header is not worth an error banner over a probe that worked.
  }
}

/* Rows ticked one by one, minus any whose whole vendor is ticked. */
function selectedTokens() {
  return [...state.selected.values()]
    .filter((t) => t.promotable && !state.selectedGroups.has(t.ats))
    .map(({ ats, token }) => ({ ats, token }));
}

function addCount() {
  const fromGroups = [...state.selectedGroups]
    .reduce((sum, vendor) => sum + (state.groups.find((g) => g.ats === vendor)?.addable ?? 0), 0);
  return fromGroups + selectedTokens().length;
}

async function enableTokens() {
  const tokens = selectedTokens();
  const vendors = [...state.selectedGroups];
  const count = addCount();
  if (!count) return;
  const interval = Number(nodes.addInterval.value);
  const every = nodes.addInterval.selectedOptions[0]?.textContent ?? "";
  if (count >= CONFIRM_ADD_AT && !window.confirm(
    `Add ${formatCount(count)} tokens to feed, polled ${every.toLowerCase()}?` +
      (vendors.length ? `\n\nVendors: ${vendors.join(", ")}` : ""),
  )) return;
  nodes.promote.disabled = true;
  try {
    const result = await api("/api/tokens/enable", {
      body: { tokens, vendors, filter: state.filter, q: state.q || null, interval_s: interval },
    });
    showError(null);
    state.selected.clear();
    state.selectedGroups.clear();
    await load();
    await loadHeartbeat();
    note(`Added ${formatCount(result.imported)} to feed`);
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

/* --- render -------------------------------------------------------------- */

function render(payload) {
  state.total = payload.total ?? 0;
  state.groups = payload.groups ?? [];
  renderCounts(payload.counts ?? {});
  populateAtsFilter();
  renderRows();
  nodes.empty.hidden = state.groups.length > 0;
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
      `${formatCount(heartbeat.last_promoted)} added to feed`;

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
    ? "Resume scheduled token validation and adding to feed"
    : "Stop scheduled token validation and adding to feed until resumed";
}

/* Rebuilds the vendor dropdown from whatever is loaded. Preserves the current
   selection unless the newly loaded set no longer has that vendor (e.g. the
   status filter changed underneath it), in which case it falls back to all. */
function populateAtsFilter() {
  const vendors = state.groups.map((group) => group.ats).sort();
  // A vendor the search or filter emptied out still shows while selected, so
  // the dropdown never silently jumps back to "All vendors" under the user.
  if (state.ats && !vendors.includes(state.ats)) vendors.push(state.ats);
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

/* Grouped by vendor, largest first (server order). Every group starts
   closed; opening one shows a page of its rows with a pager at both ends. */
function renderRows() {
  const fragment = document.createDocumentFragment();
  for (const { ats: vendor, count } of visibleGroups()) {
    const collapsed = state.expanded !== vendor;
    fragment.append(groupRow(vendor, count, collapsed));
    if (collapsed) continue;
    if (!state.rows.length) {
      fragment.append(el("tr", { class: "tok-page-note" },
        el("td", { colspan: COLUMN_COUNT, class: "secondary" }, state.pageLoading ? "Loading…" : "No tokens")));
      continue;
    }
    for (const row of state.rows) fragment.append(renderRow(row));
    if (count > PAGE_SIZE) fragment.append(pagerRow(vendor, count));
  }
  nodes.body.replaceChildren(fragment);
}

function pager(vendor, count) {
  const first = state.offset + 1;
  const last = Math.min(state.offset + PAGE_SIZE, count);
  const step = (delta) => (event) => {
    event.stopPropagation();
    loadPage(vendor, state.offset + delta);
  };
  return el(
    "span",
    { class: "tok-pager", onclick: (event) => event.stopPropagation() },
    el("button", {
      class: "btn small", type: "button", title: "First page",
      disabled: state.offset === 0 || state.pageLoading, onclick: step(-state.offset),
    }, "«"),
    el("button", {
      class: "btn small", type: "button", title: "Previous page",
      disabled: state.offset === 0 || state.pageLoading, onclick: step(-PAGE_SIZE),
    }, "‹"),
    el("span", { class: "tok-pager-range" },
      `${formatCount(first)}–${formatCount(last)} of ${formatCount(count)}`),
    el("button", {
      class: "btn small", type: "button", title: "Next page",
      disabled: last >= count || state.pageLoading, onclick: step(PAGE_SIZE),
    }, "›"),
    el("button", {
      class: "btn small", type: "button", title: "Last page",
      disabled: last >= count || state.pageLoading,
      onclick: step((Math.ceil(count / PAGE_SIZE) - 1) * PAGE_SIZE - state.offset),
    }, "»"),
  );
}

function pagerRow(vendor, count) {
  return el("tr", { class: "tok-pager-row" },
    el("td", { colspan: COLUMN_COUNT }, pager(vendor, count)));
}

function validateButton(row) {
  const button = el(
    "button",
    { class: "btn small", type: "button", title: "Check if this token is still live" },
    "Check"
  );
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

function groupRow(vendor, count, collapsed) {
  const addable = state.groups.find((group) => group.ats === vendor)?.addable ?? 0;
  const groupCheck = el("input", {
    type: "checkbox",
    class: "tok-group-check",
    disabled: addable === 0,
    "aria-label": `Select every addable ${vendor} token`,
    title: addable
      ? `Select all ${formatCount(addable)} addable ${vendor} tokens under this filter`
      : "Nothing here can be added to feed",
    onclick: (event) => event.stopPropagation(),
    onchange: (event) => {
      if (event.target.checked) state.selectedGroups.add(vendor);
      else state.selectedGroups.delete(vendor);
      renderRows();
      syncPromote();
    },
  });
  groupCheck.checked = state.selectedGroups.has(vendor);

  const row = el(
    "tr",
    { class: "group-row" },
    el(
      "td",
      { colspan: COLUMN_COUNT },
      el(
        "span",
        { style: "display:flex;align-items:center;gap:12px" },
        groupCheck,
        el("span", {}, `${collapsed ? "▸" : "▾"} ${vendor}`),
        el("span", { class: "secondary" },
          `(${formatCount(count)})` + (addable && addable !== count ? ` · ${formatCount(addable)} addable` : "")),
        !collapsed && count > PAGE_SIZE ? el("span", { class: "grow" }) : null,
        !collapsed && count > PAGE_SIZE ? pager(vendor, count) : null,
      )
    )
  );
  row.addEventListener("click", () => {
    // Opening one closes whichever was open; clicking the open one closes it.
    openGroup(collapsed ? vendor : null);
    syncPromote();
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
    `${formatCount(counts.promotable)} live, not in feed`,
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

// `feed_entered`/`feed_exited` are stored as verbs and shown as direction.
const EVENT_GLYPHS = { feed_entered: "→", feed_exited: "←" };

/* Both tails read `token_log`, one row per thing that happened to a token, and
   differ only in which verbs they keep. Rendered oldest-at-top so new lines
   arrive at the bottom, and the scroll only follows when the reader is already
   parked there -- scrolling back through history should stay put. */
function renderTail(lines, dot, rows, available, emptyText, unavailableText) {
  dot.dataset.state = available ? "live" : "off";
  if (!rows.length) {
    lines.replaceChildren(
      el("div", { class: "tok-log-empty" }, available ? emptyText : unavailableText)
    );
    return;
  }
  const pinned = lines.scrollHeight - lines.scrollTop - lines.clientHeight < 12;
  lines.replaceChildren(
    ...[...rows].reverse().map((row) =>
      el(
        "div",
        { class: "tok-log-line" },
        el("time", { title: row.checked_at ?? "" }, (row.checked_at ?? "").slice(11, 19)),
        el(
          "span",
          { class: "tok-log-st", "data-status": row.status, title: row.status },
          EVENT_GLYPHS[row.status] ?? row.status
        ),
        el("span", { class: "tok-log-token", title: `${row.ats}:${row.token}` }, row.token),
        el("span", { class: "tok-log-vendor" }, row.ats)
      )
    )
  );
  if (pinned) lines.scrollTop = lines.scrollHeight;
}

function renderLog() {
  renderTail(
    nodes.logLines,
    nodes.logDot,
    state.checks,
    state.logAvailable,
    "no checks recorded yet…",
    "check log unavailable"
  );
  renderTail(
    nodes.feedLogLines,
    nodes.feedLogDot,
    state.feedEvents,
    state.feedLogAvailable,
    "no feed changes recorded yet…",
    "feed log unavailable"
  );
}

async function loadLog() {
  try {
    const view = await api(`/api/tokens/log?limit=${LOG_LIMIT}`);
    state.logAvailable = Boolean(view.log_available);
    state.checks = view.checks ?? [];
    state.feedLogAvailable = Boolean(view.feed_log_available);
    state.feedEvents = view.feed_events ?? [];
  } catch {
    state.logAvailable = false;
    state.feedLogAvailable = false;
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
    disabled: !tokenPromotable(row) || state.selectedGroups.has(row.ats),
    onchange: (event) => {
      if (event.target.checked) {
        state.selected.set(key, { ats: row.ats, token: row.token, promotable: tokenPromotable(row) });
      } else {
        state.selected.delete(key);
      }
      syncPromote();
    },
  });
  check.checked = state.selected.has(key) || (tokenPromotable(row) && state.selectedGroups.has(row.ats));

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
  const groups = visibleGroups();
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  nodes.range.textContent = state.ats
    ? `${formatCount(total)} matching tokens for ${state.ats}`
    : `${formatCount(total)} matching tokens · ${formatCount(groups.length)} vendors`;
}

function syncPromote() {
  const count = addCount();
  nodes.promote.disabled = count === 0;
  nodes.promote.textContent = count ? `Add ${formatCount(count)} to feed` : "Add to feed";
  const parts = [];
  if (state.selectedGroups.size) {
    parts.push(`${formatCount(state.selectedGroups.size)} vendor${state.selectedGroups.size === 1 ? "" : "s"}`);
  }
  const tokens = selectedTokens().length;
  if (tokens) parts.push(`${formatCount(tokens)} token${tokens === 1 ? "" : "s"}`);
  nodes.selectionCount.textContent = parts.length ? `${parts.join(" + ")} selected` : "";
  // The header box covers the page on screen -- the only rows with boxes.
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
    if (checked) state.selected.set(key, { ats: row.ats, token: row.token, promotable: true });
    else state.selected.delete(key);
  }
  renderRows();
  syncPromote();
});

nodes.atsFilter.addEventListener("change", (event) => {
  state.ats = event.target.value;
  // Picking a vendor opens it; going back to all vendors closes everything.
  openGroup(state.ats || null);
  renderRange();
  syncPromote();
});

/* A new filter, search or order describes a different set of rows: the
   selection no longer matches what is on screen, so it goes too. */
function resetView() {
  state.selected.clear();
  state.selectedGroups.clear();
  state.offset = 0;
  load();
  syncPromote();
}

nodes.filter.addEventListener("change", (event) => {
  state.filter = event.target.value;
  resetView();
});

nodes.order.addEventListener("change", (event) => {
  state.order = event.target.value;
  resetView();
});

let searchTimer = null;
nodes.search.addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const q = event.target.value.trim();
    if (q === state.q) return;
    state.q = q;
    resetView();
  }, SEARCH_DEBOUNCE_MS);
});

nodes.promote.addEventListener("click", enableTokens);
nodes.heartbeatRun.addEventListener("click", runHeartbeat);
nodes.heartbeatPause.addEventListener("click", toggleHeartbeatPause);

/* Validate-unknown sweep --------------------------------------------------- */

// Fast while a sweep moves so progress never looks stale; slow when idle,
// where the only thing changing is the backlog count.
const SWEEP_ACTIVE_MS = 2_000;
const SWEEP_IDLE_MS = 30_000;
const SWEEP_EVENTS_KEY = "tokens-sweep-events";

const sweepNodes = {
  section: document.getElementById("sweep"),
  meta: document.getElementById("sweep-meta"),
  headline: document.getElementById("sweep-headline"),
  bar: document.getElementById("sweep-bar"),
  barFill: document.getElementById("sweep-bar-fill"),
  idleControls: document.getElementById("sweep-idle-controls"),
  vendor: document.getElementById("sweep-vendor"),
  limit: document.getElementById("sweep-limit"),
  start: document.getElementById("sweep-start"),
  pause: document.getElementById("sweep-pause"),
  cancel: document.getElementById("sweep-cancel"),
  vendors: document.getElementById("sweep-vendors"),
  foot: document.getElementById("sweep-foot"),
  unclearLink: document.getElementById("sweep-unclear-link"),
  eventsToggle: document.getElementById("sweep-events-toggle"),
  events: document.getElementById("sweep-events"),
};

const sweep = {
  snapshot: null,
  timer: null,
  wasActive: false,
  showEvents: readShowEvents(),
};

function readShowEvents() {
  try {
    return localStorage.getItem(SWEEP_EVENTS_KEY) !== "hidden";
  } catch {
    return true;
  }
}

function scheduleSweep() {
  clearTimeout(sweep.timer);
  sweep.timer = setTimeout(loadSweep, sweepActive(sweep.snapshot) ? SWEEP_ACTIVE_MS : SWEEP_IDLE_MS);
}

async function loadSweep() {
  try {
    sweep.snapshot = await api("/api/tokens/sweep");
    renderSweep();
  } catch (error) {
    // Degraded or briefly unreachable: keep the last picture, try again.
    if (!sweep.snapshot) sweepNodes.section.hidden = true;
  } finally {
    scheduleSweep();
  }
}

function headlineSpan(text, cls) {
  return el("span", cls ? { class: cls } : {}, text);
}

function sep() {
  return el("span", { class: "sep" }, "·");
}

function renderSweep() {
  const snap = sweep.snapshot;
  if (!snap) return;
  const active = sweepActive(snap);
  const backlog = snap.backlog ?? {};
  const backlogTotal = Object.values(backlog).reduce((sum, n) => sum + n, 0);
  const ran = Boolean(snap.started_at);
  sweepNodes.section.hidden = !active && !ran && backlogTotal === 0;

  // When a sweep ends, the table is stale: pick up the new statuses once.
  if (sweep.wasActive && !active) load();
  sweep.wasActive = active;

  // Meta line.
  if (active) {
    const scope = snap.ats?.length ? snap.ats.join(", ") : "all vendors";
    sweepNodes.meta.textContent = `started ${relativeTime(snap.started_at)} · ${scope} · ` +
      `${formatCount(snap.total)} tokens`;
  } else if (ran) {
    const verb = { finished: "finished", cancelled: "cancelled", failed: "failed" }[snap.status] ?? snap.status;
    sweepNodes.meta.textContent = `last sweep ${verb} ${relativeTime(snap.finished_at)} · ` +
      `${formatCount(snap.done)} checked in ${shortDuration(snap.elapsed_s)}`;
  } else {
    sweepNodes.meta.textContent = "";
  }

  // Headline.
  const parts = [];
  if (active) {
    const { pct, text } = sweepHeadline(snap);
    parts.push(el("span", {}, el("strong", {}, formatCount(snap.done)), ` / ${formatCount(snap.total)}`));
    parts.push(sep(), headlineSpan(text));
    parts.push(sep(), headlineSpan(`${formatCount(snap.live)} live`, "ok"),
      headlineSpan(`${formatCount(snap.dead)} dead`, "bad"),
      headlineSpan(`${formatCount(snap.unclear)} unclear`));
    sweepNodes.bar.hidden = false;
    sweepNodes.barFill.style.width = `${pct}%`;
  } else {
    sweepNodes.bar.hidden = true;
    if (ran && snap.status !== "idle") {
      parts.push(headlineSpan(`${formatCount(snap.live)} live`, "ok"),
        headlineSpan(`${formatCount(snap.dead)} dead`, "bad"),
        headlineSpan(`${formatCount(snap.unclear)} unclear`), sep());
    }
    const top = Object.entries(backlog).slice(0, 3)
      .map(([ats, n]) => `${ats} ${compactCount(n)}`).join(" · ");
    const more = Object.keys(backlog).length - 3;
    parts.push(el("span", {}, el("strong", {}, formatCount(backlogTotal)), " never checked"));
    if (top) parts.push(sep(), headlineSpan(more > 0 ? `${top} · +${more} vendors` : top));
  }
  if (snap.error) parts.push(sep(), headlineSpan(snap.error, "err"));
  sweepNodes.headline.replaceChildren(...parts);

  // Controls.
  sweepNodes.idleControls.hidden = active;
  sweepNodes.pause.hidden = !active;
  sweepNodes.cancel.hidden = !active;
  sweepNodes.pause.textContent = snap.status === "paused" ? "Resume" : "Pause";
  sweepNodes.start.disabled = backlogTotal === 0;
  syncSweepVendorOptions(backlog);

  renderSweepVendors(snap, active || ran);
  renderSweepFoot(snap, active || ran);
}

function syncSweepVendorOptions(backlog) {
  const current = sweepNodes.vendor.value;
  const wanted = ["", ...Object.keys(backlog).sort()];
  const have = [...sweepNodes.vendor.options].map((option) => option.value);
  if (wanted.join("|") === have.join("|")) return;
  sweepNodes.vendor.replaceChildren(
    el("option", { value: "" }, "All vendors"),
    ...Object.keys(backlog).sort().map((ats) =>
      el("option", { value: ats }, `${ats} (${compactCount(backlog[ats])})`)),
  );
  sweepNodes.vendor.value = wanted.includes(current) ? current : "";
}

function vendorState(vendor) {
  if (vendor.state === "cooling") return `cooling · ${clock(vendor.cooling_s)}`;
  if (vendor.state === "failing") return "gave up";
  return vendor.state;
}

function renderSweepVendors(snap, show) {
  const { active, done } = sweepVendorRows(snap.vendors);
  sweepNodes.vendors.hidden = !show || (!active.length && !done.length);
  if (sweepNodes.vendors.hidden) return;
  const cells = [
    el("span", { class: "hd" }, "vendor"), el("span", { class: "hd" }),
    el("span", { class: "hd num" }, "checked"), el("span", { class: "hd num rate" }, "rate"),
    el("span", { class: "hd" }, "state"),
  ];
  for (const vendor of active) {
    const pct = vendor.total ? (100 * vendor.done) / vendor.total : 0;
    cells.push(
      el("span", {}, vendor.ats),
      el("div", { class: "tok-sweep-vbar", "data-state": vendor.state },
        el("span", { style: `width:${pct.toFixed(1)}%` })),
      el("span", { class: "num" }, `${compactCount(vendor.done)} / ${compactCount(vendor.total)}`),
      el("span", { class: "num rate" }, vendor.state === "running" ? sweepRate(vendor.rate) : "–"),
      el("span", { class: "tok-sweep-state", "data-state": vendor.state }, vendorState(vendor)),
    );
  }
  if (done.length) {
    const names = done.length > 6 ? `${done.slice(0, 6).join(", ")}, …` : done.join(", ");
    cells.push(el("span", { class: "tok-sweep-done-row" },
      `✓ ${done.length} vendor${done.length === 1 ? "" : "s"} done — ${names}`));
  }
  sweepNodes.vendors.replaceChildren(...cells);
}

function renderSweepFoot(snap, show) {
  sweepNodes.foot.hidden = !show;
  if (!show) {
    sweepNodes.events.hidden = true;
    return;
  }
  sweepNodes.unclearLink.hidden = !snap.unclear;
  sweepNodes.unclearLink.textContent = `${formatCount(snap.unclear)} unclear → view in table`;
  const events = snap.events ?? [];
  sweepNodes.eventsToggle.hidden = events.length === 0;
  sweepNodes.eventsToggle.textContent = sweep.showEvents ? "hide events" : `show events (${events.length})`;
  sweepNodes.events.hidden = !sweep.showEvents || events.length === 0;
  if (sweepNodes.events.hidden) return;
  sweepNodes.events.replaceChildren(...events.map((event) => el("div", {},
    el("time", {}, new Date(event.at).toLocaleTimeString([], { hour12: false })),
    el("span", { class: event.tone }, event.vendor),
    el("span", {}, event.text),
  )));
}

async function sweepAction(path, body) {
  for (const button of [sweepNodes.start, sweepNodes.pause, sweepNodes.cancel]) button.disabled = true;
  try {
    sweep.snapshot = await api(path, { method: "POST", ...(body === undefined ? {} : { body }) });
    renderSweep();
  } catch (error) {
    showError(error);
  } finally {
    for (const button of [sweepNodes.pause, sweepNodes.cancel]) button.disabled = false;
    scheduleSweep();
  }
}

sweepNodes.start.addEventListener("click", () => {
  const limit = sweepNodes.limit.value ? Number(sweepNodes.limit.value) : null;
  const ats = sweepNodes.vendor.value ? [sweepNodes.vendor.value] : null;
  sweepAction("/api/tokens/sweep/start", { limit, ats });
});
sweepNodes.pause.addEventListener("click", () => {
  sweepAction("/api/tokens/sweep/pause", { paused: sweep.snapshot?.status !== "paused" });
});
sweepNodes.cancel.addEventListener("click", () => sweepAction("/api/tokens/sweep/cancel"));
sweepNodes.eventsToggle.addEventListener("click", (event) => {
  event.preventDefault();
  sweep.showEvents = !sweep.showEvents;
  try {
    localStorage.setItem(SWEEP_EVENTS_KEY, sweep.showEvents ? "shown" : "hidden");
  } catch {
    // Private mode: the toggle still works for this visit.
  }
  renderSweep();
});
sweepNodes.unclearLink.addEventListener("click", (event) => {
  event.preventDefault();
  nodes.filter.value = "unknown";
  nodes.filter.dispatchEvent(new Event("change"));
  document.getElementById("tokens-table").scrollIntoView({ behavior: "smooth" });
});

load();
loadHeartbeat();
loadLog();
loadSweep();
setInterval(loadHeartbeat, 30_000);
setInterval(loadLog, LOG_REFRESH_MS);
