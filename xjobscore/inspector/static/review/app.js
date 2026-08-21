/* Review: opus triage queue over the CS-internship recon set.

   Scope is every job_posting carrying attributes.opus -- a title-only regex
   pass that first tagged tech / non-tech / ambiguous / other. This page is
   where a human corrects it: j/k moves a cursor, 1/2/3/4 writes a bucket and
   advances. Every write stamps opus_by='human' and opus_at, and leaves
   opus_machine untouched, so the machine guess and the correction rate stay
   visible even after review. The default view is the queue that has to
   drain -- unreviewed rows -- same idea as Postings' "Unlabeled" default. */

import { api, isDegraded } from "/static/shared/api.js";
import { el, relativeTime, formatCount } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Review");

const PAGE = 50;
const PREFS_KEY = "inspector-review-prefs";
const LABEL_KEYS = { 1: "tech", 2: "non-tech", 3: "ambiguous", 4: "other" };
const LABELS = ["tech", "non-tech", "ambiguous", "other"];
const REVIEW_STATES = ["unreviewed", "reviewed", "changed", "all"];
const ORDERS = ["newest", "company", "title"];

const prefs = (() => {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) ?? {}; } catch { return {}; }
})();

const state = {
  source: null,             // null = all vendors
  label: "all",              // bucket filter: all | tech | non-tech | ambiguous | other
  reviewState: REVIEW_STATES.includes(prefs.reviewState) ? prefs.reviewState : "unreviewed",
  order: ORDERS.includes(prefs.order) ? prefs.order : "newest",
  search: "",
  rows: [],
  total: 0,
  cursor: -1,
  labelCounts: { tech: 0, "non-tech": 0, ambiguous: 0, other: 0, total: 0 },
  stateCounts: { all: 0, unreviewed: 0, reviewed: 0, changed: 0 },
  detailCache: new Map(),   // id -> detail payload, insertion-order eviction
  loadToken: 0,             // api.js has no AbortController; stale guard instead
  detailTimer: null,
  // Single-level undo: the last write this tab made, so `u` can put a
  // fat-fingered keystroke back. Re-applying the previous label is itself a
  // new human write (opus_by stays 'human') -- undoing a row's very first
  // review does not restore it to 'machine' provenance. Good enough for a
  // typo, not a full history.
  lastAction: null,
};

function savePrefs() {
  localStorage.setItem(PREFS_KEY, JSON.stringify({
    reviewState: state.reviewState, order: state.order,
  }));
}

const listEl = document.getElementById("review-list");
const emptyEl = document.getElementById("review-empty");
const countEl = document.getElementById("count-info");
const loadMoreBtn = document.getElementById("load-more");
const progressEl = document.getElementById("review-progress");
const searchInput = document.getElementById("review-search");
const sourceSelect = document.getElementById("source-select");
const stateSelect = document.getElementById("state-select");
const orderSelect = document.getElementById("order-select");
const pillsEl = document.getElementById("label-pills");
const helpDialog = document.getElementById("key-help");
const degradedBanner = document.getElementById("degraded-banner");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");

/* ---- resizable split (postings.js pattern) ---- */
const masterEl = document.getElementById("master");
const handleEl = document.getElementById("split-handle");
const LIST_WIDTH_KEY = "inspector-review-list-width";
const storedWidth = Number(localStorage.getItem(LIST_WIDTH_KEY));
if (storedWidth) masterEl.style.setProperty("--list-width", `${storedWidth}px`);
handleEl.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  handleEl.setPointerCapture(event.pointerId);
  handleEl.classList.add("dragging");
  const masterLeft = masterEl.getBoundingClientRect().left;
  function onMove(moveEvent) {
    const min = 320;
    const max = Math.max(min, masterEl.clientWidth - 480);
    const width = Math.min(max, Math.max(min, moveEvent.clientX - masterLeft));
    masterEl.style.setProperty("--list-width", `${width}px`);
  }
  function onUp(upEvent) {
    handleEl.releasePointerCapture(upEvent.pointerId);
    handleEl.classList.remove("dragging");
    handleEl.removeEventListener("pointermove", onMove);
    handleEl.removeEventListener("pointerup", onUp);
    const width = parseFloat(getComputedStyle(masterEl).getPropertyValue("--list-width"));
    if (width) localStorage.setItem(LIST_WIDTH_KEY, String(Math.round(width)));
  }
  handleEl.addEventListener("pointermove", onMove);
  handleEl.addEventListener("pointerup", onUp);
});

/* ---- fetching ---- */

function matchesFilters(row) {
  if (state.label !== "all" && row.opus !== state.label) return false;
  if (state.reviewState === "all") return true;
  const by = row.opus_by ?? "machine";
  if (state.reviewState === "unreviewed") return by === "machine";
  if (state.reviewState === "reviewed") return by === "human";
  if (state.reviewState === "changed") return by === "human" && row.opus !== row.opus_machine;
  return true;
}

function queryParams(offset) {
  const params = new URLSearchParams({
    label: state.label,
    state: state.reviewState,
    search: state.search,
    order: state.order,
    limit: String(PAGE),
    offset: String(offset),
  });
  if (state.source) params.set("source", state.source);
  return params;
}

function showError(error) {
  errorBanner.hidden = !error;
  if (error) errorText.textContent = error.message ?? String(error);
}

async function load(preferredCursor = 0) {
  const token = ++state.loadToken;
  try {
    const payload = await api(`/api/review?${queryParams(0)}`);
    if (token !== state.loadToken) return;
    degradedBanner.hidden = true;
    showError(null);
    state.rows = payload.rows;
    state.total = payload.total;
    state.labelCounts = payload.label_counts;
    state.stateCounts = payload.state_counts;
    state.cursor = state.rows.length
      ? Math.min(Math.max(0, preferredCursor), state.rows.length - 1)
      : -1;
    fillSources(payload.sources, payload.filters.source);
    renderList();
    renderChrome();
    followCursor();
  } catch (error) {
    if (token !== state.loadToken) return;
    if (isDegraded(error)) {
      degradedBanner.hidden = false;
      state.rows = [];
      state.total = 0;
      renderList();
      renderChrome();
    } else {
      showError(error);
    }
  }
}

async function loadMore() {
  // A row labeled this session can drop out of the current filter, which
  // would shift OFFSET under us; compensate by how many rendered rows no
  // longer match, so the next page starts where the server thinks we are.
  const drift = state.rows.filter((row) => !matchesFilters(row)).length;
  const token = ++state.loadToken;
  const payload = await api(`/api/review?${queryParams(state.rows.length - drift)}`);
  if (token !== state.loadToken) return;
  const seen = new Set(state.rows.map((row) => row.id));
  state.rows.push(...payload.rows.filter((row) => !seen.has(row.id)));
  state.total = payload.total;
  state.labelCounts = payload.label_counts;
  state.stateCounts = payload.state_counts;
  renderList();
  renderChrome();
}

function moreAvailable() {
  const drift = state.rows.filter((row) => !matchesFilters(row)).length;
  return state.rows.length - drift < state.total;
}

function fillSources(sources, current) {
  sourceSelect.replaceChildren(
    el("option", { value: "" }, "All sources"),
    ...sources.map((source) => {
      const option = el("option", { value: source }, source);
      if (source === current) option.selected = true;
      return option;
    })
  );
}

/* ---- list rendering ---- */

const rowNodes = [];

function labelBadge(label) {
  return label ? el("span", { class: "rev-label", dataset: { label } }, label) : null;
}

function entryNode(row, index) {
  const changed = row.opus_by === "human" && row.opus !== row.opus_machine;
  const node = el("div", { class: "plist-entry", role: "option", dataset: { index: String(index) } },
    el("div", { class: "plist-line1" },
      el("span", { class: "plist-title" }, row.title),
      labelBadge(row.opus),
      changed ? el("span", { class: "badge" }, `was ${row.opus_machine ?? "—"}`) : null,
      row.cs_field ? el("span", { class: "badge" }, "CS") : null,
      row.ck_list ? el("span", { class: "badge" }, row.ck_list) : null),
    el("div", { class: "plist-line2 muted" },
      [row.company, row.location_text, relativeTime(row.posted_at), row.source]
        .filter(Boolean).join(" · "))
  );
  node.addEventListener("click", () => setCursor(index));
  return node;
}

function renderList() {
  rowNodes.length = 0;
  state.rows.forEach((row, index) => rowNodes.push(entryNode(row, index)));
  listEl.replaceChildren(...rowNodes);
  emptyEl.hidden = state.rows.length > 0;
  applyCursor();
}

function applyCursor() {
  rowNodes.forEach((node, index) => {
    node.setAttribute("aria-selected", String(index === state.cursor));
  });
  const node = rowNodes[state.cursor];
  if (node) node.scrollIntoView({ block: "nearest" });
}

function refreshRow(index) {
  const node = entryNode(state.rows[index], index);
  const pending = rowNodes[index].dataset;
  if (pending.pending) node.dataset.pending = pending.pending;
  if (pending.saveFailed) node.dataset.saveFailed = pending.saveFailed;
  rowNodes[index].replaceWith(node);
  rowNodes[index] = node;
  applyCursor();
}

function renderChrome() {
  const counts = state.stateCounts;
  progressEl.textContent = counts.all
    ? `${formatCount(counts.reviewed)} reviewed · ${formatCount(counts.unreviewed)} left`
    : "";
  countEl.textContent = state.total
    ? `${formatCount(state.rows.length)} of ${formatCount(state.total)} loaded`
    : "";
  loadMoreBtn.hidden = !moreAvailable();
  pillsEl.replaceChildren(
    ...["all", ...LABELS].map((value) => {
      const count = value === "all" ? state.labelCounts.total : state.labelCounts[value];
      return el("button", {
        class: "pill", type: "button",
        "aria-pressed": String(state.label === value),
        onclick: () => { state.label = value; load(); },
      }, `${value} ${formatCount(count)}`);
    })
  );
}

/* ---- cursor + detail pane ---- */

function setCursor(index) {
  if (index < 0 || index >= state.rows.length) return;
  state.cursor = index;
  applyCursor();
  followCursor();
  if (index >= state.rows.length - 1 && moreAvailable()) loadMore();
}

function followCursor() {
  const row = state.rows[state.cursor];
  renderDetail(row ?? null, null);
  if (!row) return;
  clearTimeout(state.detailTimer);
  state.detailTimer = setTimeout(async () => {
    const detail = await fetchDetail(row.id);
    if (state.rows[state.cursor]?.id === row.id && detail) renderDetail(row, detail);
    const next = state.rows[state.cursor + 1];
    if (next) fetchDetail(next.id); // prefetch
  }, 80);
}

async function fetchDetail(id) {
  if (state.detailCache.has(id)) return state.detailCache.get(id);
  try {
    const detail = await api(`/api/review/${id}`);
    state.detailCache.set(id, detail);
    if (state.detailCache.size > 200) {
      state.detailCache.delete(state.detailCache.keys().next().value);
    }
    return detail;
  } catch {
    return null;
  }
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function descriptionText(value) {
  if (!value) return "";
  if (!value.includes("<")) return value;
  const broken = value.replace(/<\/(p|li|h[1-6]|div)>|<br\s*\/?>/gi, "$&\n");
  return new DOMParser().parseFromString(broken, "text/html").body.textContent.trim();
}

const FIELD_ORDER = [
  ["company", "company"], ["location_text", "location"], ["status", "status"],
  ["job_type", "job type"], ["posted_at", "posted"],
  ["cs_field", "CS degree field"], ["ck_list", "ck_list"],
  ["source", "source"], ["opus_at", "reviewed"],
];

const actionButtons = Array.from(document.querySelectorAll("#detail-actions button"));

function renderDetail(row, detail) {
  const titleEl = document.getElementById("detail-title");
  const labelEl = document.getElementById("detail-label");
  const diffEl = document.getElementById("detail-diff");
  const linksEl = document.getElementById("detail-links");
  const fieldsEl = document.getElementById("detail-fields");
  const descEl = document.getElementById("detail-desc");
  if (!row) {
    titleEl.textContent = "Select a posting";
    labelEl.replaceChildren();
    diffEl.hidden = true;
    linksEl.replaceChildren();
    fieldsEl.replaceChildren();
    descEl.textContent = "";
    actionButtons.forEach((button) => { button.disabled = true; button.setAttribute("aria-pressed", "false"); });
    return;
  }
  const full = { ...row, ...(detail ?? {}) };
  titleEl.textContent = full.title;
  labelEl.replaceChildren(
    labelBadge(full.opus),
    el("span", { class: "muted" }, full.opus_by === "human" ? " reviewed" : " machine guess")
  );
  const changed = full.opus_by === "human" && full.opus !== full.opus_machine;
  diffEl.hidden = !changed;
  if (changed) diffEl.textContent = `machine guessed "${full.opus_machine}" → you set "${full.opus}"`;
  actionButtons.forEach((button) => {
    button.disabled = false;
    button.setAttribute("aria-pressed", String(button.dataset.label === full.opus));
  });
  linksEl.replaceChildren(
    ...[["open", full.source_url], ["apply", full.apply_url]]
      .map(([text, value]) => {
        const url = safeUrl(value);
        return url
          ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text)
          : null;
      })
  );
  fieldsEl.replaceChildren(
    ...FIELD_ORDER.flatMap(([key, name]) => {
      let value = full[key];
      if (key === "cs_field") value = value ? "yes" : "no";
      if (value === null || value === undefined || value === "") return [];
      if (key === "posted_at" || key === "opus_at") value = `${relativeTime(value)} (${value})`;
      return [el("dt", {}, name), el("dd", {}, String(value))];
    })
  );
  if (detail === null) {
    descEl.textContent = full.has_description ? "Loading description…" : "";
  } else {
    descEl.textContent = descriptionText(full.description) || "No description.";
  }
}

/* ---- label writes: immediate, optimistic, retry by re-press ---- */

async function writeLabel(index, label) {
  const row = state.rows[index];
  if (!row) return;
  const previous = { opus: row.opus, opus_by: row.opus_by, opus_at: row.opus_at };
  state.lastAction = { index, id: row.id, previousLabel: previous.opus };
  row.opus = label;
  row.opus_by = "human";
  row.opus_at = new Date().toISOString();
  const node = rowNodes[index];
  node.dataset.pending = "true";
  delete node.dataset.saveFailed;
  refreshRow(index);
  try {
    await api("/api/review/labels", { body: { labels: [{ id: row.id, label }] } });
    delete rowNodes[index].dataset.pending;
    // Local tallies; the next load() reconciles with the server's counts.
    const labelCounts = state.labelCounts;
    if (previous.opus && labelCounts[previous.opus] !== undefined) labelCounts[previous.opus] -= 1;
    if (labelCounts[label] !== undefined) labelCounts[label] += 1;
    const stateCounts = state.stateCounts;
    if ((previous.opus_by ?? "machine") === "machine") {
      stateCounts.unreviewed = Math.max(0, stateCounts.unreviewed - 1);
      stateCounts.reviewed += 1;
    }
    if (row.opus !== row.opus_machine) stateCounts.changed += 1;
    state.detailCache.delete(row.id);
    refreshRow(index);
    renderChrome();
  } catch (error) {
    row.opus = previous.opus;
    row.opus_by = previous.opus_by;
    row.opus_at = previous.opus_at;
    const failed = rowNodes[index];
    delete failed.dataset.pending;
    failed.dataset.saveFailed = "true";
    refreshRow(index);
    countEl.textContent = `save failed (${error.message}) — press the key again`;
  }
  if (state.rows[state.cursor] === row) followCursor();
}

function undo() {
  const action = state.lastAction;
  if (!action || state.rows[action.index]?.id !== action.id) return;
  state.lastAction = null;
  writeLabel(action.index, action.previousLabel);
}

/* ---- keyboard ---- */

document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target;
  const typing = target instanceof Element && target.closest("input, select, textarea");
  if (event.key === "Escape") {
    if (helpDialog.open) helpDialog.close();
    else if (typing) target.blur();
    return;
  }
  if (typing || helpDialog.open) return;
  if (event.key === "?") { helpDialog.showModal(); event.preventDefault(); return; }
  if (event.key === "/") { searchInput.focus(); event.preventDefault(); return; }
  if (event.key === "j" || event.key === "ArrowDown") {
    setCursor(state.cursor + 1); event.preventDefault(); return;
  }
  if (event.key === "k" || event.key === "ArrowUp") {
    setCursor(state.cursor - 1); event.preventDefault(); return;
  }
  if (event.key === "u") { undo(); event.preventDefault(); return; }
  const label = LABEL_KEYS[event.key];
  if (label && state.cursor >= 0) {
    writeLabel(state.cursor, label);
    setCursor(state.cursor + 1);
    event.preventDefault();
    return;
  }
  if (event.key === "o" && state.cursor >= 0) {
    const row = state.rows[state.cursor];
    const url = safeUrl(row.source_url) ?? safeUrl(row.apply_url);
    if (url) window.open(url, "_blank", "noopener,noreferrer");
    event.preventDefault();
  }
});

/* ---- controls ---- */

sourceSelect.addEventListener("change", () => {
  state.source = sourceSelect.value || null;
  load();
});
stateSelect.addEventListener("change", () => {
  state.reviewState = stateSelect.value;
  savePrefs();
  load();
});
orderSelect.addEventListener("change", () => {
  state.order = orderSelect.value;
  savePrefs();
  load();
});
let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = searchInput.value.trim();
    load();
  }, 220);
});
loadMoreBtn.addEventListener("click", () => loadMore());
document.getElementById("help-btn").addEventListener("click", () => {
  if (helpDialog.open) helpDialog.close();
  else helpDialog.showModal();
});
actionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (state.cursor < 0) return;
    writeLabel(state.cursor, button.dataset.label);
  });
});

// Restore persisted control positions before first load.
stateSelect.value = state.reviewState;
orderSelect.value = state.order;

// Top-level await keeps the load event behind the first full render.
await load().catch((error) => {
  emptyEl.hidden = false;
  emptyEl.textContent = `Failed to load review queue: ${error.message}`;
});
