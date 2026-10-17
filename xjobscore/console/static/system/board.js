/* Console page — the scheduler board.
 *
 * The server sends vendor summaries, never the whole fleet: each vendor row
 * opens into one line per status (err, stale, new, paused, ok) with its
 * columns rolled up, and a status line opens into its feeds, 25 to a page.
 * Small error lines open on their own. A fleet search (two characters or
 * more) asks the server and shows each matching vendor's hits instead.
 *
 * Vendor-wide controls -- interval presets, fetch, pause -- sit on the last
 * status line of an open vendor. Board groups are filed from each vendor
 * row's "move to" menu; a group exists while something is filed in it.
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el, formatCount, formatInterval } from "/static/shared/format.js";
import { setProviderPaused } from "/static/shared/feed-policy.js";
import {
  ATTENTION_STATES,
  INTERVAL_PRESETS,
  STATE_LABEL,
  UNGROUPED,
  UNGROUPED_NAME,
  addSection,
  dropEmptySections,
  dueInfo,
  intervalRange,
  jobsToday,
  moveSource,
  pageWindow,
  parseSearch,
  providerRollup,
  renameSection,
  sectionCounts,
  sectionKey,
  sectionMatcher,
  sectionOf,
  sectionProviders,
  sortProviders,
  uniqueSectionName,
  visibleBuckets,
} from "/static/system/model.mjs";
import { selectFeed, syncSelectedRow } from "/static/system/drawer.js";
import { paintFeedWorkers } from "/static/system/live.js";

const AUTO_OPEN_ERRORS = 12;
const SEARCH_MIN = 2;
const SEARCH_DEBOUNCE_MS = 250;
const FILTER_DEBOUNCE_MS = 250;
const OPEN_KEY = "scheduler-open-providers";
const LINES_KEY = "scheduler-open-lines";

const tableEl = document.getElementById("sched-table");
const emptyEl = document.getElementById("sched-empty");
const countEl = document.getElementById("row-count");
const moveMenuEl = document.getElementById("move-menu");

const board = {
  providers: [],
  loaded: false,
  error: null,
  telemetryAvailable: false,
  layout: [],
  groupsAvailable: false,
  savingGroups: false,
  view: "all",
  search: "",
  sort: { key: "status", direction: "asc" },
  open: loadSet(OPEN_KEY),
  // "source state" -> { page, q, data, loading, error }; open lines only.
  lines: new Map([...loadSet(LINES_KEY)].map((key) => [key, freshLine()])),
  autoOpened: new Set(),
  results: null,
  searchSeq: 0,
  busy: new Set(),
  moveMenu: null,
  renaming: null,
  hooks: { connected: () => true, toast: () => {}, onChanged: async () => {} },
};

function freshLine() {
  return { page: 1, q: "", data: null, loading: false, error: null, seq: 0 };
}

function loadSet(key) {
  try {
    const stored = JSON.parse(localStorage.getItem(key) ?? "[]");
    return new Set(Array.isArray(stored) ? stored : []);
  } catch {
    return new Set();
  }
}

function saveSet(key, values) {
  try {
    localStorage.setItem(key, JSON.stringify([...values]));
  } catch {
    /* open state just will not survive a reload */
  }
}

function persistOpen() {
  saveSet(OPEN_KEY, board.open);
  saveSet(LINES_KEY, board.lines.keys());
}

const lineKey = (source, state) => `${source} ${state}`;

export function initBoard(hooks) {
  Object.assign(board.hooks, hooks);
}

/* ---------- formatting ---------- */

/* "26s", "5m", "3h", "2d" — the terse age used throughout the board. */
function terseAgo(iso) {
  if (!iso) return "never";
  const seconds = (Date.now() - Date.parse(iso)) / 1000;
  if (Number.isNaN(seconds)) return iso;
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172_800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

const formatIvl = (seconds) => formatInterval(seconds).replace(/\s+/g, "");

function agoRange(newest, oldest) {
  if (!newest) return "never";
  const recent = terseAgo(newest);
  const old = terseAgo(oldest);
  return !oldest || recent === old ? recent : `${recent}–${old}`;
}

function stateMark(state, title = null) {
  return el("span", { class: "ops-st", "data-state": state, title }, STATE_LABEL[state] ?? state);
}

function dueCell(dueAt, held) {
  const due = dueInfo(dueAt, held, Date.now());
  return el(
    "td",
    {
      class: `num ops-next${due.imminent ? " is-imminent" : ""}`,
      dataset: dueAt && !held ? { dueAt } : undefined,
    },
    held ? el("span", { class: "muted" }, "paused") : due.text
  );
}

function jobsCell(value) {
  return el("td", { class: "num" }, value == null ? el("span", { class: "muted" }, "—") : formatCount(value));
}

function errorsCell(value) {
  return el("td", { class: "num ops-errors-cell" }, value ? el("span", { class: "ops-err" }, `x${formatCount(value)}`) : null);
}

/* ---------- loading ---------- */

export async function loadProviders() {
  try {
    const view = await api("/api/scheduler/providers");
    board.providers = view.providers ?? [];
    board.telemetryAvailable = Boolean(view.telemetry_available);
    board.error = null;
    board.loaded = true;
  } catch (error) {
    board.error = error.message;
    throw error;
  } finally {
    renderBoard();
  }
}

export async function loadGroups() {
  try {
    const view = await api("/api/scheduler/groups");
    board.layout = view.groups ?? [];
    board.groupsAvailable = Boolean(view.available);
  } catch {
    board.groupsAvailable = false;
  }
  renderBoard();
}

async function loadLine(source, state) {
  const line = board.lines.get(lineKey(source, state));
  if (!line) return;
  line.loading = true;
  const seq = ++line.seq;
  const params = new URLSearchParams({ state, page: String(line.page) });
  if (line.q) params.set("q", line.q);
  try {
    const data = await api(`/api/scheduler/providers/${encodeURIComponent(source)}/feeds?${params}`);
    // A newer request for this line (page or filter changed) wins.
    if (seq !== line.seq) return;
    line.data = data;
    line.page = data.page;
    line.error = null;
  } catch (error) {
    if (seq !== line.seq) return;
    line.error = error.message;
  } finally {
    if (seq === line.seq) {
      line.loading = false;
      if (board.lines.get(lineKey(source, state)) === line) renderBoard();
    }
  }
}

async function runSearch() {
  const { text } = parseSearch(board.search);
  const seq = ++board.searchSeq;
  if (text.length < SEARCH_MIN) {
    board.results = null;
    renderBoard();
    return;
  }
  board.results = { query: text, providers: board.results?.providers ?? [], loading: true, error: null };
  renderBoard();
  try {
    const view = await api(`/api/scheduler/search?${new URLSearchParams({ q: text })}`);
    if (seq !== board.searchSeq) return;
    board.results = { query: text, providers: view.providers ?? [], loading: false, error: null };
  } catch (error) {
    if (seq !== board.searchSeq) return;
    board.results = { query: text, providers: [], loading: false, error: error.message };
  }
  renderBoard();
}

/* Everything the board shows, re-read: summaries, open lines, search hits. */
export async function refreshBoard() {
  const lines = [...board.lines.keys()].map((key) => key.split(" "));
  await Promise.allSettled([
    loadProviders(),
    loadGroups(),
    ...lines.map(([source, state]) => loadLine(source, state)),
    board.results ? runSearch() : null,
  ]);
}

/* ---------- open / close ---------- */

function openLine(source, state) {
  const key = lineKey(source, state);
  if (!board.lines.has(key)) board.lines.set(key, freshLine());
  persistOpen();
  loadLine(source, state);
}

function closeLine(source, state) {
  board.lines.delete(lineKey(source, state));
  persistOpen();
}

function setProviderOpen(provider, open) {
  if (open) {
    board.open.add(provider.source);
    // A short error line is what you opened the vendor to see.
    const errors = provider.counts?.backing_off ?? 0;
    if (!board.autoOpened.has(provider.source) && errors && errors <= AUTO_OPEN_ERRORS) {
      board.autoOpened.add(provider.source);
      openLine(provider.source, "backing_off");
    }
  } else {
    board.open.delete(provider.source);
  }
  persistOpen();
}

export function expandAll() {
  for (const provider of shownProviders()) setProviderOpen(provider, true);
  renderBoard();
}

export function collapseAll() {
  board.open.clear();
  persistOpen();
  renderBoard();
}

export function setView(view) {
  board.view = view;
  renderBoard();
}

export function setSort(key) {
  board.sort = board.sort.key === key
    ? { key, direction: board.sort.direction === "asc" ? "desc" : "asc" }
    : { key, direction: "asc" };
  renderBoard();
}

let searchTimer = null;
export function setSearch(raw) {
  board.search = raw.trim();
  clearTimeout(searchTimer);
  renderBoard();
  searchTimer = setTimeout(runSearch, SEARCH_DEBOUNCE_MS);
}

/* ---------- vendor-wide actions ---------- */

async function vendorAction(provider, label, run) {
  if (board.busy.has(provider.source)) return;
  board.busy.add(provider.source);
  renderBoard();
  try {
    const message = await run();
    if (message) board.hooks.toast(message);
    await board.hooks.onChanged();
  } catch (error) {
    board.hooks.toast(`Could not ${label} ${provider.source}: ${error.message}`, "error");
  } finally {
    board.busy.delete(provider.source);
    renderBoard();
  }
}

function setVendorInterval(provider, seconds, label) {
  const total = providerRollup(provider).total;
  if (!window.confirm(`Poll all ${formatCount(total)} ${provider.source} feeds every ${label}?`)) return;
  vendorAction(provider, "change the interval of", async () => {
    const result = await api(`/api/scheduler/providers/${encodeURIComponent(provider.source)}/interval`, {
      body: { interval_s: seconds },
    });
    return `${formatCount(result.affected)} ${provider.source} feeds now poll every ${label}.`;
  });
}

function fetchVendor(provider) {
  if (!window.confirm(`Bring every ${provider.source} feed forward to poll now?`)) return;
  vendorAction(provider, "fetch", async () => {
    const result = await api("/api/scheduler/poll-now", { body: { source: provider.source } });
    return `${formatCount(result.updated)} ${provider.source} feeds brought forward.`;
  });
}

function pauseVendor(provider, paused) {
  const verb = paused ? "Pause" : "Resume";
  const total = providerRollup(provider).total;
  if (!window.confirm(`${verb} all ${formatCount(total)} ${provider.source} feeds?`)) return;
  vendorAction(provider, verb.toLowerCase(), async () => {
    const result = await setProviderPaused(provider.source, paused);
    return `${verb}d all ${formatCount(result.affected)} ${provider.source} feeds.`;
  });
}

/* ---------- groups: save, move menu, rename ---------- */

/* Every edit writes the whole layout; the previous one is the undo. */
async function saveLayout(next, message = null, { undoable = true } = {}) {
  if (board.savingGroups) return;
  const previous = board.layout;
  board.savingGroups = true;
  try {
    const view = await api("/api/scheduler/groups", {
      method: "PUT",
      body: { groups: dropEmptySections(next).map(({ id, name, sources }) => ({ id, name, sources })) },
    });
    board.layout = view.groups ?? [];
    board.groupsAvailable = true;
    if (message) {
      board.hooks.toast(message, "ok", undoable
        ? { label: "undo", run: () => saveLayout(previous, "Undone.", { undoable: false }) }
        : null);
    }
  } catch (error) {
    if (isDegraded(error)) board.groupsAvailable = false;
    board.hooks.toast(`Could not save groups: ${error.message}`, "error");
  } finally {
    board.savingGroups = false;
    renderBoard();
  }
}

function sectionName(id) {
  if (id === UNGROUPED) return UNGROUPED_NAME;
  return board.layout.find((group) => sectionKey(group.id) === id)?.name ?? UNGROUPED_NAME;
}

function nameTaken(name, exceptId = null) {
  const folded = name.toLowerCase();
  return folded === UNGROUPED_NAME.toLowerCase() || board.layout.some(
    (group) => sectionKey(group.id) !== exceptId && group.name.toLowerCase() === folded
  );
}

function openMoveMenu(source, anchor) {
  closeMoveMenu();
  board.moveMenu = { source, anchor, creating: false };
  anchor.setAttribute("aria-expanded", "true");
  renderMoveMenu();
}

export function closeMoveMenu({ restoreFocus = false } = {}) {
  if (!board.moveMenu) return false;
  const { anchor } = board.moveMenu;
  anchor.setAttribute("aria-expanded", "false");
  board.moveMenu = null;
  moveMenuEl.hidden = true;
  moveMenuEl.replaceChildren();
  if (restoreFocus) anchor.focus();
  return true;
}

function renderMoveMenu() {
  const menu = board.moveMenu;
  if (!menu) return;
  const current = sectionOf(board.layout, menu.source);
  const targets = [
    ...board.layout.map((group) => ({ id: sectionKey(group.id), name: group.name })),
    { id: UNGROUPED, name: UNGROUPED_NAME },
  ];
  const items = targets.map((target) =>
    el(
      "button",
      {
        class: "ops-move-item",
        type: "button",
        role: "menuitem",
        disabled: target.id === current ? "" : null,
        onclick: () => {
          closeMoveMenu();
          saveLayout(moveSource(board.layout, menu.source, target.id), `${menu.source} moved to ${target.name}.`);
        },
      },
      target.name
    )
  );
  let tail;
  if (menu.creating) {
    const input = el("input", {
      class: "ops-move-input",
      type: "text",
      maxlength: "64",
      value: uniqueSectionName(board.layout),
      "aria-label": "New group name",
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        const name = input.value.trim();
        if (!name || nameTaken(name)) {
          input.setCustomValidity(name ? "That name is taken." : "Enter a name.");
          input.reportValidity();
          return;
        }
        closeMoveMenu();
        saveLayout(addSection(board.layout, name, [menu.source]), `${menu.source} moved to ${name}.`);
      } else if (event.key === "Escape") {
        event.stopPropagation();
        closeMoveMenu({ restoreFocus: true });
      } else {
        input.setCustomValidity("");
      }
    });
    tail = input;
    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  } else {
    tail = el(
      "button",
      {
        class: "ops-move-item ops-move-new",
        type: "button",
        role: "menuitem",
        onclick: (event) => {
          event.stopPropagation();
          menu.creating = true;
          renderMoveMenu();
        },
      },
      "new group…"
    );
  }
  moveMenuEl.replaceChildren(el("div", { class: "ops-move-title" }, `move ${menu.source} to`), ...items, tail);
  moveMenuEl.hidden = false;
  // Below the button when it fits, above it near the bottom of the window.
  const rect = menu.anchor.getBoundingClientRect();
  const height = moveMenuEl.offsetHeight;
  const width = moveMenuEl.offsetWidth;
  const below = rect.bottom + 4 + height <= window.innerHeight;
  moveMenuEl.style.top = `${Math.round(below ? rect.bottom + 4 : Math.max(4, rect.top - 4 - height))}px`;
  moveMenuEl.style.left = `${Math.round(Math.min(rect.left, window.innerWidth - width - 8))}px`;
}

export function moveMenuContains(node) {
  return moveMenuEl.contains(node);
}

function startRename(id) {
  board.renaming = id;
  renderBoard();
}

export function cancelRename() {
  if (board.renaming == null) return false;
  board.renaming = null;
  renderBoard();
  return true;
}

function finishRename(id, value) {
  board.renaming = null;
  const name = value.trim();
  const before = sectionName(id);
  if (!name || name === before) {
    renderBoard();
    return;
  }
  if (nameTaken(name, id)) {
    board.hooks.toast(`"${name}" is already a group name.`, "error");
    renderBoard();
    return;
  }
  saveLayout(renameSection(board.layout, id, name), `${before} renamed to ${name}.`);
}

/* ---------- rows ---------- */

function breakdown(provider, total) {
  const parts = [];
  for (const bucket of visibleBuckets(provider, "all")) {
    parts.push(
      el("span", {}, el("b", { "data-state": bucket.state }, formatCount(bucket.count)), ` ${STATE_LABEL[bucket.state]}`),
      " / "
    );
  }
  parts.push(el("span", {}, el("b", {}, formatCount(total)), " feeds"));
  return el("span", { class: "ops-breakdown" }, parts);
}

function sectionRow(section) {
  const renaming = board.renaming === section.id;
  const counts = sectionCounts(section);
  const total = counts.reduce((sum, [, count]) => sum + count, 0);
  let name;
  if (renaming) {
    name = el("input", {
      class: "ops-section-input",
      type: "text",
      maxlength: "64",
      value: section.name,
      "aria-label": "Group name",
    });
    name.addEventListener("keydown", (event) => {
      if (event.key === "Enter") finishRename(section.id, name.value);
      if (event.key === "Escape") {
        event.stopPropagation();
        cancelRename();
      }
    });
    name.addEventListener("blur", () => board.renaming === section.id && finishRename(section.id, name.value));
    requestAnimationFrame(() => {
      name.focus();
      name.select();
    });
  } else if (!section.ungrouped && board.groupsAvailable) {
    name = el(
      "button",
      { class: "ops-section-name", type: "button", title: "Rename this group", onclick: () => startRename(section.id) },
      section.name
    );
  } else {
    name = el("span", { class: "ops-section-name" }, section.name);
  }
  return el(
    "tr",
    { class: "ops-section-row" },
    el(
      "td",
      { colspan: "7" },
      name,
      el(
        "span",
        { class: "ops-section-sum" },
        counts.flatMap(([state, count]) => [el("b", { "data-state": state }, formatCount(count)), ` ${STATE_LABEL[state]} / `]),
        el("b", {}, formatCount(total)),
        " feeds"
      )
    )
  );
}

function providerRow(provider, open) {
  const rollup = providerRollup(provider);
  const move = board.groupsAvailable
    ? el(
        "button",
        {
          class: "ops-move",
          type: "button",
          "aria-haspopup": "menu",
          "aria-expanded": "false",
          title: "File this vendor in a group",
          onclick: (event) => {
            event.stopPropagation();
            if (board.moveMenu?.source === provider.source) closeMoveMenu();
            else openMoveMenu(provider.source, event.currentTarget);
          },
        },
        "move to ▾"
      )
    : null;
  return el(
    "tr",
    {
      class: "ops-provider-row",
      "aria-expanded": String(open),
      tabindex: "0",
      dataset: { source: provider.source },
      onclick: (event) => {
        if (event.target.closest("button, input")) return;
        setProviderOpen(provider, !open);
        renderBoard();
      },
      onkeydown: (event) => {
        if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        setProviderOpen(provider, !open);
        renderBoard();
      },
    },
    el("td", {}, rollup.worst ? stateMark(rollup.worst) : null),
    el(
      "td",
      { class: "ops-provider-cell" },
      el("span", { class: "ops-chevron", "aria-hidden": "true" }, "▶"),
      el("span", { class: "ops-provider-name" }, provider.source),
      breakdown(provider, rollup.total),
      move
    ),
    el("td", { class: "num" }, intervalRange(rollup.intervalMin, rollup.intervalMax, formatIvl)),
    dueCell(rollup.dueMin, !rollup.dueMin && (provider.counts?.paused ?? 0) === rollup.total),
    el("td", { class: "num" }, terseAgo(rollup.lastMax)),
    jobsCell(rollup.jobs),
    errorsCell(rollup.errors)
  );
}

function vendorControls(provider) {
  const rollup = providerRollup(provider);
  const busy = board.busy.has(provider.source);
  const writable = board.hooks.connected() && !busy;
  const shared = rollup.intervalMin === rollup.intervalMax ? rollup.intervalMin : null;
  const allPaused = (provider.counts?.paused ?? 0) === rollup.total;
  return el(
    "span",
    { class: "ops-ctl", onclick: (event) => event.stopPropagation() },
    el("span", { class: "ops-ctl-k" }, "interval"),
    INTERVAL_PRESETS.map(([seconds, label]) =>
      el(
        "button",
        {
          class: "ops-row-btn",
          type: "button",
          "aria-pressed": String(seconds === shared),
          disabled: !writable || seconds === shared ? "" : null,
          title: `Poll every ${provider.source} feed every ${label}`,
          onclick: () => setVendorInterval(provider, seconds, label),
        },
        label
      )
    ),
    el("span", { class: "ops-ctl-gap" }),
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: writable && !allPaused ? null : "",
        title: `Bring every ${provider.source} feed forward to poll now`,
        onclick: () => fetchVendor(provider),
      },
      "fetch"
    ),
    el(
      "button",
      {
        class: "ops-row-btn",
        type: "button",
        disabled: writable ? null : "",
        onclick: () => pauseVendor(provider, !allPaused),
      },
      busy ? "…" : allPaused ? "resume" : "pause"
    )
  );
}

function lineRow(provider, bucket, open, controls) {
  return el(
    "tr",
    {
      class: "ops-line-row",
      "aria-expanded": String(open),
      tabindex: "0",
      dataset: { source: provider.source, state: bucket.state },
      onclick: (event) => {
        if (event.target.closest("button, input")) return;
        if (open) closeLine(provider.source, bucket.state);
        else openLine(provider.source, bucket.state);
        renderBoard();
      },
      onkeydown: (event) => {
        if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        if (open) closeLine(provider.source, bucket.state);
        else openLine(provider.source, bucket.state);
        renderBoard();
      },
    },
    el("td", {}),
    el(
      "td",
      { class: "ops-line-cell" },
      el("span", { class: "ops-chevron", "aria-hidden": "true" }, "▶"),
      stateMark(bucket.state),
      el("span", { class: "ops-line-count" }, formatCount(bucket.count)),
      controls ? vendorControls(provider) : null
    ),
    el("td", { class: "num" }, intervalRange(bucket.interval_min, bucket.interval_max, formatIvl)),
    dueCell(bucket.due_min, bucket.state === "paused"),
    el("td", { class: "num" }, agoRange(bucket.last_max, bucket.last_min)),
    jobsCell(bucket.jobs),
    errorsCell(bucket.errors)
  );
}

function feedRow(feed, { showState = false } = {}) {
  const held = feed.policy?.enabled === false;
  return el(
    "tr",
    {
      class: "ops-feed-row",
      tabindex: "0",
      dataset: { feedRef: feed.feed_ref },
      "aria-selected": "false",
      onclick: (event) => selectFeed(feed, event.currentTarget),
      onkeydown: (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        selectFeed(feed, event.currentTarget);
      },
    },
    el("td", {}, showState ? stateMark(feed.sched.status.state, feed.sched.status.detail) : null),
    el(
      "td",
      { class: "ops-feed-cell" },
      el("span", { class: "ops-feed-name" }, feed.company_name || feed.key),
      el("span", { class: "ops-feed-key" }, feed.key)
    ),
    el("td", { class: "num" }, feed.policy?.interval_s ? formatIvl(feed.policy.interval_s) : "—"),
    dueCell(feed.policy?.due_at, held),
    el(
      "td",
      { class: "num ops-last-cell" },
      el("span", { class: "ops-last" }, terseAgo(feed.policy?.last_poll_at)),
      el("span", { class: "ops-polling", dataset: { workerFeed: `${feed.source}:${feed.key}` } })
    ),
    jobsCell(jobsToday(feed)),
    errorsCell(feed.telemetry?.error_count_7d)
  );
}

function noteRow(text, tone = null) {
  return el("tr", { class: "ops-note-row" }, el("td", {}), el("td", { colspan: "6", class: tone }, text));
}

function pagerRow(source, state, line) {
  const data = line.data;
  const filter = el("input", {
    class: "ops-line-filter",
    type: "search",
    placeholder: `filter ${STATE_LABEL[state]}…`,
    value: line.q,
    "aria-label": `Filter ${source} ${STATE_LABEL[state]} feeds`,
    dataset: { focusKey: `filter ${source} ${state}` },
  });
  let timer = null;
  filter.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      line.q = filter.value.trim();
      line.page = 1;
      loadLine(source, state);
    }, FILTER_DEBOUNCE_MS);
  });
  const goto = (page) => {
    line.page = page;
    loadLine(source, state);
  };
  const from = data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = Math.min(data.total, data.page * data.page_size);
  const links = pageWindow(data.page, data.pages).map((page) =>
    page == null
      ? el("span", {}, "…")
      : page === data.page
        ? el("b", {}, formatCount(page))
        : el("button", { class: "ops-linkish", type: "button", onclick: () => goto(page) }, formatCount(page))
  );
  return el(
    "tr",
    { class: "ops-pager-row" },
    el("td", {}),
    el(
      "td",
      { colspan: "6" },
      filter,
      el("span", { class: "ops-pager-range" }, `${formatCount(from)}–${formatCount(to)} of ${formatCount(data.total)}`),
      el(
        "span",
        { class: "ops-pager" },
        el("button", { class: "ops-linkish", type: "button", disabled: data.page <= 1 ? "" : null, onclick: () => goto(data.page - 1), "aria-label": "Previous page" }, "‹"),
        links,
        el("button", { class: "ops-linkish", type: "button", disabled: data.page >= data.pages ? "" : null, onclick: () => goto(data.page + 1), "aria-label": "Next page" }, "›")
      )
    )
  );
}

function lineRows(provider, bucket) {
  const line = board.lines.get(lineKey(provider.source, bucket.state));
  if (!line) return [];
  if (!line.data) return [noteRow(line.error ? `Could not load feeds: ${line.error}` : "loading…", line.error ? "ops-note-bad" : "muted")];
  const rows = [];
  if (line.data.pages > 1 || line.q) rows.push(pagerRow(provider.source, bucket.state, line));
  if (line.error) rows.push(noteRow(`Could not refresh: ${line.error}`, "ops-note-bad"));
  if (!line.data.feeds.length) rows.push(noteRow(line.q ? "No feeds match." : "No feeds in this status now.", "muted"));
  rows.push(...line.data.feeds.map((feed) => feedRow(feed)));
  return rows;
}

function matchRows(provider, hit) {
  const rows = [
    el(
      "tr",
      { class: "ops-line-row ops-match-row", "aria-expanded": "true" },
      el("td", {}),
      el(
        "td",
        { class: "ops-line-cell" },
        el("span", { class: "ops-chevron", "aria-hidden": "true" }, "▶"),
        el("span", { class: "ops-match-k" }, "matches"),
        el("span", { class: "ops-line-count" }, formatCount(hit.count)),
        hit.count > hit.feeds.length
          ? el("span", { class: "muted ops-match-more" }, `first ${hit.feeds.length} shown; narrow the search for the rest`)
          : null
      ),
      el("td", { colspan: "5" })
    ),
  ];
  rows.push(...hit.feeds.map((feed) => feedRow(feed, { showState: true })));
  return rows;
}

/* ---------- table ---------- */

function shownProviders() {
  const { group } = parseSearch(board.search);
  const inSection = sectionMatcher(board.layout, group);
  const hits = board.results ? new Set(board.results.providers.map((hit) => hit.source)) : null;
  return board.providers.filter((provider) =>
    (!inSection || inSection(provider.source))
    && (!hits || hits.has(provider.source))
    && visibleBuckets(provider, board.view).length
  );
}

function renderViewCounts() {
  const totals = { all: 0, issues: 0, failing: 0, paused: 0 };
  for (const provider of board.providers) {
    for (const [state, count] of Object.entries(provider.counts ?? {})) {
      totals.all += count;
      if (ATTENTION_STATES.has(state)) totals.issues += count;
      if (state === "backing_off") totals.failing += count;
      if (state === "paused") totals.paused += count;
    }
  }
  for (const button of document.querySelectorAll("[data-view]")) {
    const name = button.dataset.view;
    button.setAttribute("aria-pressed", String(board.view === name));
    button.querySelector("span").textContent = board.loaded ? formatCount(totals[name]) : "";
  }
  return totals;
}

function renderSortHeaders() {
  for (const th of tableEl.querySelectorAll("th[data-sort]")) {
    if (board.sort.key === th.dataset.sort) {
      th.setAttribute("aria-sort", board.sort.direction === "desc" ? "descending" : "ascending");
    } else {
      th.removeAttribute("aria-sort");
    }
  }
}

/* Re-renders keep focus in a line's filter box while the operator types. */
function captureFocus() {
  const active = document.activeElement;
  const key = active?.dataset?.focusKey;
  return key ? { key, start: active.selectionStart, end: active.selectionEnd } : null;
}

function restoreFocus(focus) {
  if (!focus) return;
  const input = [...tableEl.querySelectorAll("[data-focus-key]")].find((node) => node.dataset.focusKey === focus.key);
  if (!input) return;
  input.focus();
  try {
    input.setSelectionRange(focus.start, focus.end);
  } catch {
    /* search inputs without selection support */
  }
}

export function renderBoard() {
  const totals = renderViewCounts();
  renderSortHeaders();
  const providers = sortProviders(shownProviders(), board.sort);
  const hits = board.results ? new Map(board.results.providers.map((hit) => [hit.source, hit])) : null;
  const sections = sectionProviders(providers, board.layout);
  const headed = board.layout.length > 0;
  const focus = captureFocus();
  const body = el("tbody", {});

  let shown = 0;
  for (const section of sections) {
    if (headed) body.append(sectionRow(section));
    for (const provider of section.providers) {
      const open = hits ? true : board.open.has(provider.source);
      shown += providerRollup(provider).total;
      body.append(providerRow(provider, open));
      if (!open) continue;
      if (hits) {
        body.append(...matchRows(provider, hits.get(provider.source)));
        continue;
      }
      const buckets = visibleBuckets(provider, board.view);
      buckets.forEach((bucket, index) => {
        const lineOpen = board.lines.has(lineKey(provider.source, bucket.state));
        body.append(lineRow(provider, bucket, lineOpen, index === buckets.length - 1));
        if (lineOpen) body.append(...lineRows(provider, bucket));
      });
    }
  }
  for (const old of [...tableEl.tBodies]) old.remove();
  tableEl.append(body);
  restoreFocus(focus);

  if (!board.loaded) countEl.textContent = "";
  else if (hits) {
    const matched = [...hits.values()].reduce((sum, hit) => sum + hit.count, 0);
    countEl.textContent = `${formatCount(matched)} matches · ${formatCount(providers.length)} providers`;
  } else {
    countEl.textContent = `${formatCount(shown)}/${formatCount(totals.all)} shown · ${formatCount(providers.length)} providers`;
  }
  emptyEl.hidden = providers.length > 0;
  if (!providers.length) {
    emptyEl.textContent = board.error
      ? `Could not load the board: ${board.error}`
      : !board.loaded
        ? "Loading feeds…"
        : board.results?.loading
          ? "Searching…"
          : board.results?.error
            ? `Search failed: ${board.results.error}`
            : board.results || board.search
              ? "Nothing matches. Clear the search or the view filter."
              : board.providers.length
                ? "No feeds in this view."
                : "No feeds are enabled. Enable a feed from the Feeds page to schedule it.";
  }
  syncSelectedRow();
  paintFeedWorkers();
  // Rows are rebuilt on every render: keep an open move menu on its vendor.
  if (board.moveMenu && !tableEl.contains(board.moveMenu.anchor)) {
    const anchor = [...tableEl.querySelectorAll("tr.ops-provider-row")]
      .find((row) => row.dataset.source === board.moveMenu.source)
      ?.querySelector(".ops-move");
    if (anchor) {
      board.moveMenu.anchor = anchor;
      anchor.setAttribute("aria-expanded", "true");
      if (!board.moveMenu.creating) renderMoveMenu();
    } else {
      closeMoveMenu();
    }
  }
}

/* Next-poll countdowns tick between refreshes. */
export function tickCountdowns() {
  const now = Date.now();
  for (const cell of tableEl.querySelectorAll("td[data-due-at]")) {
    const due = dueInfo(cell.dataset.dueAt, false, now);
    cell.textContent = due.text;
    cell.classList.toggle("is-imminent", due.imminent);
  }
}
