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
const REVIEW_KEYS = { 1: "correct", 2: "incorrect", 3: "needs_review" };
const REVIEW_STATES = ["unreviewed", "needs_review", "correct", "incorrect", "all", "hidden"];

const prefs = (() => {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) ?? {}; } catch { return {}; }
})();

const state = {
  source: null,          // null = all vendors
  status: "active",
  labelState: LABEL_STATES.includes(prefs.labelState) ? prefs.labelState : "all",
  reviewState: REVIEW_STATES.includes(prefs.reviewState) ? prefs.reviewState : "unreviewed",
  search: "",
  internSignal: Boolean(prefs.internSignal),
  order: ["newest", "company", "random"].includes(prefs.order) ? prefs.order : "newest",
  seed: "",
  rows: [],
  total: 0,
  cursor: -1,
  labelCounts: { relevant: 0, irrelevant: 0, unsure: 0, total: 0 },
  reviewCounts: { all: 0, unreviewed: 0, correct: 0, incorrect: 0, needs_review: 0, hidden: 0 },
  expandedGroups: new Set(),
  selectedMemberId: null,
  detailCache: new Map(),  // id -> detail payload, insertion-order eviction
  loadToken: 0,            // api.js has no AbortController; stale guard instead
  detailTimer: null,
};

function savePrefs() {
  localStorage.setItem(PREFS_KEY, JSON.stringify({
    labelState: state.labelState, reviewState: state.reviewState,
    order: state.order, internSignal: state.internSignal,
  }));
}

function duplicateMode() {
  return state.status === "duplicates";
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
const noteLabelEl = document.querySelector("label[for='detail-note']");

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
  if (duplicateMode()) {
    const params = new URLSearchParams({
      review_state: state.reviewState,
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

async function load(preferredCursor = 0) {
  const token = ++state.loadToken;
  const endpoint = duplicateMode() ? "/api/duplicate-groups" : "/api/postings";
  const payload = await api(`${endpoint}?${queryParams(0)}`);
  if (token !== state.loadToken) return;
  state.rows = duplicateMode() ? payload.groups : payload.rows;
  state.total = payload.total;
  state.seed = payload.filters.seed;
  if (duplicateMode()) state.reviewCounts = payload.review_counts;
  else state.labelCounts = payload.label_counts;
  state.cursor = state.rows.length
    ? Math.min(Math.max(0, preferredCursor), state.rows.length - 1)
    : -1;
  state.selectedMemberId = null;
  state.expandedGroups = new Set();
  fillSources(payload.sources, payload.filters.source);
  renderList();
  renderChrome();
  followCursor();
}

async function loadMore() {
  // Rows labeled this session can drop out of the server-side filter, which
  // would shift OFFSET under us; compensate by how many rendered rows no
  // longer match, so the next page starts where the server thinks we are.
  const drift = duplicateMode()
    ? 0
    : state.rows.filter((row) => !matchesLabelFilter(row)).length;
  const token = ++state.loadToken;
  const endpoint = duplicateMode() ? "/api/duplicate-groups" : "/api/postings";
  const payload = await api(`${endpoint}?${queryParams(state.rows.length - drift)}`);
  if (token !== state.loadToken) return;
  const incoming = duplicateMode() ? payload.groups : payload.rows;
  const seen = new Set(state.rows.map((row) => row.id));
  state.rows.push(...incoming.filter((row) => !seen.has(row.id)));
  state.total = payload.total;
  if (duplicateMode()) state.reviewCounts = payload.review_counts;
  else state.labelCounts = payload.label_counts;
  renderList();
  renderChrome();
}

function moreAvailable() {
  const drift = duplicateMode()
    ? 0
    : state.rows.filter((row) => !matchesLabelFilter(row)).length;
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

function postingEntryNode(row, index) {
  const attrs = row.attributes ?? {};
  const node = el("div", { class: "plist-entry", role: "option", dataset: { index: String(index) } },
    el("div", { class: "plist-line1" },
      el("span", { class: "plist-title" }, row.title),
      labelBadge(row.label),
      attrs.opportunity_type
        ? el("span", { class: "badge" }, attrs.opportunity_type)
        : null,
      row.status === "closed" ? el("span", { class: "badge" }, "closed") : null),
    el("div", { class: "plist-line2 muted" },
      [row.company, row.location_text,
       relativeTime(row.posted_at ?? row.first_seen_at), row.source]
        .filter(Boolean).join(" · "))
  );
  node.addEventListener("click", () => setCursor(index));
  return node;
}

function reviewBadge(review, changed = false) {
  const decision = review?.decision ?? (changed ? "changed" : "unreviewed");
  const text = {
    correct: "correct",
    incorrect: "not duplicates",
    needs_review: "needs review",
    hidden: "hidden",
    changed: "changed",
    unreviewed: "unreviewed",
  }[decision];
  return el("span", { class: "dup-review-badge", dataset: { decision } }, text);
}

function duplicateGroupNode(group, index) {
  const expanded = state.expandedGroups.has(group.id);
  const node = el("div", {
    class: "dup-group",
    role: "treeitem",
    "aria-expanded": String(expanded),
    dataset: { index: String(index), groupId: group.id },
  });
  const header = el("div", { class: "plist-entry dup-group-head" },
    el("div", { class: "plist-line1" },
      el("span", { class: "dup-disclosure", "aria-hidden": "true" }, expanded ? "▾" : "▸"),
      el("span", { class: "plist-title" }, group.title),
      el("span", { class: "badge" }, `${group.member_count} postings`),
      reviewBadge(group.review, group.changed_since_review)),
    el("div", { class: "plist-line2 muted" },
      [group.company, group.sources.join(", "), group.locations.join(" · ")]
        .filter(Boolean).join(" · "))
  );
  header.addEventListener("click", () => selectDuplicateGroup(index, true));
  node.append(header);

  const members = el("div", { class: "dup-members", role: "group" });
  members.hidden = !expanded;
  group.members.forEach((member) => {
    const child = el("div", {
      class: "dup-member",
      role: "treeitem",
      tabindex: "-1",
      dataset: { memberId: String(member.id) },
    },
    el("div", { class: "plist-line1" },
      el("span", { class: "plist-title" }, member.title),
      member.status === "closed"
        ? el("span", { class: "badge" }, "closed")
        : null),
    el("div", { class: "plist-line2 muted" },
      [member.source, member.location_text,
       relativeTime(member.posted_at ?? member.first_seen_at)]
        .filter(Boolean).join(" · ")));
    child.addEventListener("click", (event) => {
      event.stopPropagation();
      selectDuplicateMember(index, member.id);
    });
    members.append(child);
  });
  node.append(members);
  return node;
}

function entryNode(row, index) {
  return duplicateMode()
    ? duplicateGroupNode(row, index)
    : postingEntryNode(row, index);
}

function renderList() {
  rowNodes.length = 0;
  state.rows.forEach((row, index) => rowNodes.push(entryNode(row, index)));
  listEl.replaceChildren(...rowNodes);
  listEl.setAttribute("role", duplicateMode() ? "tree" : "listbox");
  listEl.setAttribute("aria-label", duplicateMode() ? "Duplicate groups" : "Postings");
  emptyEl.hidden = state.rows.length > 0;
  applyCursor();
}

function applyCursor() {
  rowNodes.forEach((node, index) => {
    node.setAttribute("aria-selected", String(index === state.cursor));
    if (duplicateMode()) {
      node.querySelectorAll(".dup-member").forEach((child) => {
        child.setAttribute(
          "aria-selected",
          String(index === state.cursor
            && Number(child.dataset.memberId) === state.selectedMemberId)
        );
      });
    }
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
  document.querySelectorAll(".posting-key-help").forEach((row) => {
    row.hidden = duplicateMode();
  });
  document.querySelectorAll(".duplicate-key-help").forEach((row) => {
    row.hidden = !duplicateMode();
  });
  if (duplicateMode()) {
    const counts = state.reviewCounts;
    const reviewed = Math.max(0, counts.all - counts.unreviewed);
    const decided = counts.correct + counts.incorrect;
    const correctness = decided
      ? ` · ${Math.round((counts.correct / decided) * 100)}% correct`
      : "";
    const hidden = counts.hidden ? ` · ${formatCount(counts.hidden)} hidden` : "";
    progressEl.textContent = `${formatCount(reviewed)} reviewed · ${formatCount(counts.unreviewed)} unreviewed${correctness}${hidden}`;
    countEl.textContent = state.total
      ? `${formatCount(state.rows.length)} of ${formatCount(state.total)} groups loaded`
      : "";
    loadMoreBtn.hidden = !moreAvailable();
    reshuffleBtn.hidden = state.order !== "random";
    internBtn.setAttribute("aria-pressed", String(state.internSignal));
    pillsEl.setAttribute("aria-label", "Duplicate review filter");
    pillsEl.replaceChildren(
      ...REVIEW_STATES.map((value) => {
        const count = counts[value];
        const label = value.replace("_", " ");
        return el("button", {
          class: "pill", type: "button",
          "aria-pressed": String(state.reviewState === value),
          onclick: () => { state.reviewState = value; savePrefs(); load(); },
        }, `${label} ${formatCount(count)}`);
      })
    );
    return;
  }
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
  pillsEl.setAttribute("aria-label", "Label filter");
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

function selectDuplicateGroup(index, toggle = false) {
  if (index < 0 || index >= state.rows.length) return;
  const group = state.rows[index];
  state.cursor = index;
  state.selectedMemberId = null;
  if (toggle) {
    if (state.expandedGroups.has(group.id)) state.expandedGroups.delete(group.id);
    else state.expandedGroups.add(group.id);
    renderList();
  } else {
    applyCursor();
  }
  followCursor();
  if (index >= state.rows.length - 1 && moreAvailable()) loadMore();
}

function selectDuplicateMember(index, memberId) {
  if (index < 0 || index >= state.rows.length) return;
  state.cursor = index;
  state.selectedMemberId = Number(memberId);
  state.expandedGroups.add(state.rows[index].id);
  renderList();
  followCursor();
}

function setCursor(index) {
  if (duplicateMode()) {
    selectDuplicateGroup(index, false);
    return;
  }
  if (index < 0 || index >= state.rows.length) return;
  state.cursor = index;
  applyCursor();
  followCursor();
  if (index >= state.rows.length - 1 && moreAvailable()) loadMore();
}

function followCursor() {
  if (duplicateMode()) {
    const group = state.rows[state.cursor];
    if (!group) {
      renderDetail(null, null);
      return;
    }
    const member = group.members.find((row) => row.id === state.selectedMemberId);
    if (!member) {
      renderGroupDetail(group);
      return;
    }
    renderDetail(member, null);
    clearTimeout(state.detailTimer);
    state.detailTimer = setTimeout(async () => {
      const detail = await fetchDetail(member.id);
      const current = state.rows[state.cursor];
      if (current?.id === group.id && state.selectedMemberId === member.id && detail) {
        renderDetail(member, detail);
      }
    }, 80);
    return;
  }
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

function renderGroupDetail(group) {
  const titleEl = document.getElementById("detail-title");
  const labelEl = document.getElementById("detail-label");
  const linksEl = document.getElementById("detail-links");
  const fieldsEl = document.getElementById("detail-fields");
  const attrsEl = document.getElementById("detail-attrs");
  const descEl = document.getElementById("detail-desc");

  titleEl.textContent = `${group.company} · ${group.title}`;
  labelEl.replaceChildren(reviewBadge(group.review, group.changed_since_review));
  const reviewActions = [
      ["Correct duplicates", "correct"],
      ["Not duplicates", "incorrect"],
      ["Needs review", "needs_review"],
      ["Hide from review", "hidden"],
    ].map(([text, decision]) => el("button", {
      class: "btn small", type: "button",
      onclick: () => writeGroupReview(decision),
    }, text));
  if (group.review) {
    reviewActions.push(el("button", {
      class: "btn small", type: "button",
      onclick: () => writeGroupReview(null),
    }, "Clear review"));
  }
  linksEl.replaceChildren(...reviewActions);
  const fields = [
    ["members", group.member_count],
    ["sources", group.sources.join(", ")],
    ["locations", group.locations.join(" · ") || "—"],
    ["candidate state", group.algorithm_state],
    ["latest seen", group.latest_seen_at],
    ["group id", group.id],
  ];
  fieldsEl.replaceChildren(
    ...fields.flatMap(([name, value]) => [
      el("dt", {}, name), el("dd", {}, String(value ?? "—")),
    ])
  );
  attrsEl.replaceChildren(
    ...group.warnings.flatMap((warning) => [
      el("dt", {}, "warning"),
      el("dd", { class: "dup-warning" }, warning.replaceAll("_", " ")),
    ])
  );
  noteLabelEl.textContent = "Review note";
  noteEl.value = group.review?.note ?? group.draftNote ?? "";
  noteEl.disabled = false;

  const rawTitles = new Set(group.members.map((member) => member.title));
  const table = el("table", { class: "dup-compare" },
    el("thead", {}, el("tr", {},
      ...["Source", "Title", "Location", "Status", "ATS identity", "Description", "Links"]
        .map((value) => el("th", {}, value)))),
    el("tbody", {}, ...group.members.map((member) => {
      const evidence = member.evidence ?? {};
      const sourceUrl = safeUrl(member.source_url);
      const applyUrl = safeUrl(member.apply_url);
      return el("tr", {},
        el("td", {}, member.source),
        el("td", { class: rawTitles.size > 1 ? "dup-diff" : "" }, member.title),
        el("td", {
          class: group.warnings.includes("location_mismatch") ? "dup-diff" : "",
        }, evidence.location ?? member.location_text ?? "—"),
        el("td", {}, member.status),
        el("td", {
          class: group.warnings.includes("ats_identity_mismatch") ? "dup-diff" : "",
        }, evidence.ats_identity ?? "—"),
        el("td", {
          class: group.warnings.includes("description_hash_mismatch") ? "dup-diff" : "",
        }, evidence.description_hash ? evidence.description_hash.slice(0, 10) : "—"),
        el("td", { class: "dup-links" },
          sourceUrl ? el("a", { href: sourceUrl, target: "_blank", rel: "noopener noreferrer" }, "open") : null,
          applyUrl ? el("a", { href: applyUrl, target: "_blank", rel: "noopener noreferrer" }, "apply") : null)
      );
    }))
  );
  descEl.replaceChildren(table);
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
  noteLabelEl.textContent = "Note";
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

async function writeGroupReview(decision, { advance = true } = {}) {
  const group = state.rows[state.cursor];
  if (!duplicateMode() || !group) return;
  const index = state.cursor;
  const previous = {
    review: group.review ? { ...group.review } : null,
    changed_since_review: group.changed_since_review,
  };
  const note = state.selectedMemberId === null
    ? noteEl.value.trim() || null
    : group.review?.note ?? group.draftNote ?? null;
  group.review = decision
    ? { decision, note, reviewed_at: new Date().toISOString() }
    : null;
  group.changed_since_review = false;
  renderList();
  renderGroupDetail(group);
  try {
    const response = await api(`/api/duplicate-groups/${group.id}/review`, {
      body: { decision, note },
    });
    group.fingerprint = response.fingerprint;
    group.review = response.review
      ? { ...response.review, reviewed_at: new Date().toISOString() }
      : null;
    if (duplicateMode()) await load(advance ? index : state.cursor);
  } catch (error) {
    group.review = previous.review;
    group.changed_since_review = previous.changed_since_review;
    if (duplicateMode()) {
      renderList();
      renderGroupDetail(group);
    }
    alert(`Could not save duplicate review: ${error.message}`);
  }
}

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
  if (duplicateMode()) {
    const group = state.rows[state.cursor];
    if ((event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") && group) {
      state.expandedGroups.add(group.id);
      renderList();
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowLeft" && group) {
      state.expandedGroups.delete(group.id);
      state.selectedMemberId = null;
      renderList();
      renderGroupDetail(group);
      event.preventDefault();
      return;
    }
    const decision = REVIEW_KEYS[event.key];
    if (decision && group) {
      writeGroupReview(decision);
      event.preventDefault();
      return;
    }
    if ((event.key === "0" || event.key === "x") && group) {
      writeGroupReview(null);
      event.preventDefault();
      return;
    }
    if (event.key === "o" && group) {
      const member = group.members.find((row) => row.id === state.selectedMemberId)
        ?? group.members.find((row) => row.id === group.representative_posting_id)
        ?? group.members[0];
      const url = safeUrl(member?.source_url) ?? safeUrl(member?.apply_url);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      event.preventDefault();
      return;
    }
  }
  if (event.key === "j" || event.key === "ArrowDown") {
    setCursor(state.cursor + 1); event.preventDefault(); return;
  }
  if (event.key === "k" || event.key === "ArrowUp") {
    setCursor(state.cursor - 1); event.preventDefault(); return;
  }
  if (duplicateMode()) return;
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
  state.detailCache.clear();
  state.expandedGroups = new Set();
  state.selectedMemberId = null;
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
  if (duplicateMode()) {
    if (!row || state.selectedMemberId !== null) return;
    row.draftNote = noteEl.value.trim() || null;
    if (row.review) writeGroupReview(row.review.decision, { advance: false });
  } else if (row?.label) {
    writeLabel(state.cursor, row.label, noteEl.value.trim() || null);
  }
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
    if (job.state === "done" && duplicateMode()) await load();
    return;
  }
}

dedupButton.addEventListener("click", async () => {
  if (!confirm(
    "Rebuild every duplicate group from the current job_posting rows? " +
    "This replaces the derived candidate table; Inspector reviews are retained, " +
    "and changed groups return to the unreviewed queue."
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
