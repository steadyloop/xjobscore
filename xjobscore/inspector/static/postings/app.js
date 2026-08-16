/* Postings: browser + labeling queue over public.job_posting.

   The list is a keyboard-driven queue: j/k moves a cursor, 1/2/3 writes a
   triage label (relevant / irrelevant / unsure) and advances. Labels persist
   immediately -- one small POST per keystroke -- so a dead tab never loses
   ground truth. The detail pane always follows the cursor. */

import { api } from "/static/shared/api.js";
import { el, relativeTime, formatCount } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Postings");

const PAGE = 50;
const PREFS_KEY = "inspector-postings-prefs";
const LABEL_KEYS = { 1: "relevant", r: "relevant", 2: "irrelevant", i: "irrelevant", 3: "unsure", u: "unsure" };
const LABEL_STATES = ["all", "unlabeled", "labeled", "relevant", "irrelevant", "unsure"];

const prefs = (() => {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) ?? {}; } catch { return {}; }
})();

const state = {
  source: null,          // null = all vendors
  status: "active",
  labelState: LABEL_STATES.includes(prefs.labelState) ? prefs.labelState : "all",
  search: "",
  internSignal: Boolean(prefs.internSignal),
  order: ["newest", "company", "random"].includes(prefs.order) ? prefs.order : "newest",
  seed: "",
  rows: [],
  total: 0,
  cursor: -1,
  labelCounts: { relevant: 0, irrelevant: 0, unsure: 0, total: 0 },
  detailCache: new Map(),  // id -> detail payload, insertion-order eviction
  loadToken: 0,            // api.js has no AbortController; stale guard instead
  detailTimer: null,
};

function savePrefs() {
  localStorage.setItem(PREFS_KEY, JSON.stringify({
    labelState: state.labelState, order: state.order, internSignal: state.internSignal,
  }));
}

const listEl = document.getElementById("posting-list");
const emptyEl = document.getElementById("postings-empty");
const countEl = document.getElementById("count-info");
const loadMoreBtn = document.getElementById("load-more");
const progressEl = document.getElementById("label-progress");
const searchInput = document.getElementById("posting-search");
const sourceSelect = document.getElementById("source-select");
const statusSelect = document.getElementById("status-select");
const orderSelect = document.getElementById("order-select");
const reshuffleBtn = document.getElementById("reshuffle-btn");
const internBtn = document.getElementById("intern-signal");
const pillsEl = document.getElementById("label-state-pills");
const helpDialog = document.getElementById("key-help");
const noteEl = document.getElementById("detail-note");

/* ---- resizable split (feeds.js pattern; pane is always open here) ---- */
const masterEl = document.getElementById("master");
const handleEl = document.getElementById("split-handle");
masterEl.classList.add("split-open");
handleEl.hidden = false;
const LIST_WIDTH_KEY = "inspector-postings-list-width";
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

function matchesLabelFilter(row) {
  if (state.labelState === "all") return true;
  if (state.labelState === "unlabeled") return !row.label;
  if (state.labelState === "labeled") return Boolean(row.label);
  return row.label === state.labelState;
}

function queryParams(offset) {
  const params = new URLSearchParams({
    status: state.status,
    label_state: state.labelState,
    search: state.search,
    intern_signal: String(state.internSignal),
    order: state.order,
    seed: state.seed,
    limit: String(PAGE),
    offset: String(offset),
  });
  if (state.source) params.set("source", state.source);
  return params;
}

async function load() {
  const token = ++state.loadToken;
  const payload = await api(`/api/postings?${queryParams(0)}`);
  if (token !== state.loadToken) return;
  state.rows = payload.rows;
  state.total = payload.total;
  state.seed = payload.filters.seed;
  state.labelCounts = payload.label_counts;
  state.cursor = state.rows.length ? 0 : -1;
  fillSources(payload.sources, payload.filters.source);
  renderList();
  renderChrome();
  followCursor();
}

async function loadMore() {
  // Rows labeled this session can drop out of the server-side filter, which
  // would shift OFFSET under us; compensate by how many rendered rows no
  // longer match, so the next page starts where the server thinks we are.
  const drift = state.rows.filter((row) => !matchesLabelFilter(row)).length;
  const token = ++state.loadToken;
  const payload = await api(`/api/postings?${queryParams(state.rows.length - drift)}`);
  if (token !== state.loadToken) return;
  const seen = new Set(state.rows.map((row) => row.id));
  state.rows.push(...payload.rows.filter((row) => !seen.has(row.id)));
  state.total = payload.total;
  state.labelCounts = payload.label_counts;
  renderList();
  renderChrome();
}

function moreAvailable() {
  const drift = state.rows.filter((row) => !matchesLabelFilter(row)).length;
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
  return label
    ? el("span", { class: "plist-label", "data-label": label }, label)
    : null;
}

function entryNode(row, index) {
  const attrs = row.attributes ?? {};
  const node = el("div", { class: "plist-entry", role: "option", dataset: { index: String(index) } },
    el("div", { class: "plist-line1" },
      el("span", { class: "plist-title" }, row.title),
      labelBadge(row.label),
      attrs.opportunity_type
        ? el("span", { class: "badge" }, attrs.opportunity_type)
        : null,
      row.status === "closed" ? el("span", { class: "badge", "data-kind": "error" }, "closed") : null),
    el("div", { class: "plist-line2 muted" },
      [row.company, row.location_text,
       relativeTime(row.posted_at ?? row.first_seen_at), row.source]
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
  const counts = state.labelCounts;
  progressEl.textContent = counts.total
    ? `${formatCount(counts.total)} labeled · ${formatCount(counts.relevant)} relevant`
    : "no labels yet";
  countEl.textContent = state.total
    ? `${formatCount(state.rows.length)} of ${formatCount(state.total)} loaded`
    : "";
  loadMoreBtn.hidden = !moreAvailable();
  reshuffleBtn.hidden = state.order !== "random";
  internBtn.setAttribute("aria-pressed", String(state.internSignal));
  pillsEl.replaceChildren(
    ...LABEL_STATES.map((value) => {
      const count = { relevant: counts.relevant, irrelevant: counts.irrelevant,
                      unsure: counts.unsure, labeled: counts.total }[value];
      return el("button", {
        class: "pill", type: "button",
        "aria-pressed": String(state.labelState === value),
        onclick: () => { state.labelState = value; savePrefs(); load(); },
      }, count === undefined ? value : `${value} ${formatCount(count)}`);
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
    if (state.rows[state.cursor]?.id === row.id && detail) {
      renderDetail(row, detail);
    }
    const next = state.rows[state.cursor + 1];
    if (next) fetchDetail(next.id); // prefetch
  }, 80);
}

async function fetchDetail(id) {
  if (state.detailCache.has(id)) return state.detailCache.get(id);
  try {
    const detail = await api(`/api/postings/${id}`);
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
  ["posted_at", "posted"], ["first_seen_at", "first seen"], ["last_seen_at", "last seen"],
  ["source", "source"], ["source_key", "source key"], ["id", "posting id"],
  ["raw_id", "raw id"], ["content_hash", "content hash"], ["dup_group_id", "dup group"],
  ["labeled_at", "labeled"],
];

function renderDetail(row, detail) {
  const titleEl = document.getElementById("detail-title");
  const labelEl = document.getElementById("detail-label");
  const linksEl = document.getElementById("detail-links");
  const fieldsEl = document.getElementById("detail-fields");
  const attrsEl = document.getElementById("detail-attrs");
  const descEl = document.getElementById("detail-desc");
  if (!row) {
    titleEl.textContent = "Select a posting";
    labelEl.replaceChildren();
    linksEl.replaceChildren();
    fieldsEl.replaceChildren();
    attrsEl.replaceChildren();
    descEl.textContent = "";
    noteEl.value = "";
    noteEl.disabled = true;
    return;
  }
  const full = { ...row, ...(detail ?? {}) };
  titleEl.textContent = full.title;
  labelEl.replaceChildren(labelBadge(full.label) ?? el("span", { class: "muted" }, "unlabeled"));
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
      if (value === null || value === undefined || value === "") return [];
      if (key.endsWith("_at")) value = `${relativeTime(value)} (${value})`;
      return [el("dt", {}, name), el("dd", {}, String(value))];
    })
  );
  const attrs = Object.entries(full.attributes ?? {}).filter(([key]) => key !== "version");
  attrsEl.replaceChildren(
    ...attrs.flatMap(([key, value]) => [
      el("dt", {}, key),
      el("dd", {}, Array.isArray(value) ? value.join(", ") : String(value)),
    ])
  );
  noteEl.value = full.note ?? "";
  noteEl.disabled = !full.label;
  if (detail === null) {
    descEl.textContent = full.has_description ? "Loading description…" : "";
  } else {
    descEl.textContent = descriptionText(full.description) || "No description.";
  }
}

/* ---- label writes: immediate, optimistic, retry by re-press ---- */

async function writeLabel(index, label, note) {
  const row = state.rows[index];
  if (!row) return;
  const previous = { label: row.label, note: row.note };
  row.label = label;
  if (note !== undefined) row.note = note;
  if (label === null) row.note = null;
  const node = rowNodes[index];
  node.dataset.pending = "true";
  delete node.dataset.saveFailed;
  refreshRow(index);
  try {
    await api("/api/postings/labels", {
      body: {
        labels: [{
          source: row.source,
          source_key: row.source_key,
          label,
          note: row.note ?? null,
          title_snapshot: row.title,
          company_snapshot: row.company ?? null,
        }],
      },
    });
    delete rowNodes[index].dataset.pending;
    // Local tallies; the next load() reconciles with the server's counts.
    const counts = state.labelCounts;
    if (previous.label && counts[previous.label] !== undefined) counts[previous.label] -= 1;
    if (previous.label && !label) counts.total -= 1;
    if (!previous.label && label) counts.total += 1;
    if (label && counts[label] !== undefined) counts[label] += 1;
    state.detailCache.delete(row.id);
    refreshRow(index);
    renderChrome();
  } catch (error) {
    row.label = previous.label;
    row.note = previous.note;
    const failed = rowNodes[index];
    delete failed.dataset.pending;
    failed.dataset.saveFailed = "true";
    refreshRow(index);
    countEl.textContent = `save failed (${error.message}) — press the key again`;
  }
  if (state.rows[state.cursor] === row) followCursor();
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
  const label = LABEL_KEYS[event.key];
  if (label && state.cursor >= 0) {
    writeLabel(state.cursor, label);
    setCursor(state.cursor + 1);
    event.preventDefault();
    return;
  }
  if ((event.key === "0" || event.key === "x") && state.cursor >= 0) {
    writeLabel(state.cursor, null);
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
statusSelect.addEventListener("change", () => {
  state.status = statusSelect.value;
  load();
});
orderSelect.addEventListener("change", () => {
  state.order = orderSelect.value;
  state.seed = "";
  savePrefs();
  load();
});
reshuffleBtn.addEventListener("click", () => {
  state.seed = "";
  load();
});
internBtn.addEventListener("click", () => {
  state.internSignal = !state.internSignal;
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
noteEl.addEventListener("change", () => {
  const row = state.rows[state.cursor];
  if (row?.label) writeLabel(state.cursor, row.label, noteEl.value.trim() || null);
});

/* Rebuilds job_posting_dup_group from public.job_posting -- the very table
   this page browses; a rebuilt group shows up in each row's dup group field
   after a reload. Destructive full recompute, hence the confirm. */
const DEDUP_POLL_MS = 1500;
const dedupButton = document.getElementById("dedup-button");

function describeDedup(job) {
  if (job.state === "done") {
    const c = job.counts ?? {};
    return `Rebuilt: ${c.groups ?? 0} groups, ${c.postings ?? 0} postings`;
  }
  if (job.state === "failed") return `Rebuild failed: ${job.error ?? "unknown error"}`;
  return "Rebuild duplicate groups";
}

async function pollDedup() {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, DEDUP_POLL_MS));
    const job = await api("/api/gold/dedup");
    if (job.state === "running") continue;
    dedupButton.textContent = describeDedup(job);
    dedupButton.disabled = false;
    return;
  }
}

dedupButton.addEventListener("click", async () => {
  if (!confirm(
    "Rebuild every duplicate group from the current job_posting rows? " +
    "This replaces job_posting_dup_group entirely -- any manual adjudication is lost."
  )) {
    return;
  }
  dedupButton.disabled = true;
  dedupButton.textContent = "Rebuilding…";
  try {
    await api("/api/gold/dedup", { body: {} });
    await pollDedup();
  } catch (error) {
    dedupButton.disabled = false;
    dedupButton.textContent = "Rebuild duplicate groups";
    alert(`Could not start the rebuild: ${error.message}`);
  }
});

// Restore persisted control positions before first load.
statusSelect.value = state.status;
orderSelect.value = state.order;

// Top-level await keeps the load event behind the first full render.
await load().catch((error) => {
  emptyEl.hidden = false;
  emptyEl.textContent = `Failed to load postings: ${error.message}`;
});
