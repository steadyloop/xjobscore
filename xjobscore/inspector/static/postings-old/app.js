/* Postings (Old): browser over the latest STAGED run per feed. Superseded by
   /postings, which reads public.job_posting and adds labeling; kept for
   payload-level inspection of what a fetch actually returned. */

import { api } from "/static/shared/api.js";
import { el } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Postings (Old)");

const PAGE = 50;
const state = {
  source: null, // null = backend default; "*" = all
  mode: "exclude_full_time",
  usOnly: true,
  search: "",
  offset: 0,
  total: 0,
};

const sourceSelect = document.getElementById("source-select");
const searchInput = document.getElementById("posting-search");

document.getElementById("mode-select").addEventListener("change", (event) => {
  state.mode = event.target.value;
  state.offset = 0;
  load();
});
document.getElementById("us-only").addEventListener("change", (event) => {
  state.usOnly = event.target.checked;
  state.offset = 0;
  load();
});
sourceSelect.addEventListener("change", () => {
  state.source = sourceSelect.value;
  state.offset = 0;
  load();
});
let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.search = searchInput.value.trim();
    state.offset = 0;
    load();
  }, 220);
});
document.getElementById("prev-btn").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE);
  load();
});
document.getElementById("next-btn").addEventListener("click", () => {
  if (state.offset + PAGE < state.total) {
    state.offset += PAGE;
    load();
  }
});

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

async function load() {
  const params = new URLSearchParams({
    mode: state.mode,
    us_only: String(state.usOnly),
    search: state.search,
    limit: String(PAGE),
    offset: String(state.offset),
  });
  if (state.source) params.set("source", state.source);
  const payload = await api(`/api/postings-old?${params}`);
  state.total = payload.total;

  // Source options reflect what's actually stored.
  const current = payload.source ?? "*";
  sourceSelect.replaceChildren(
    el("option", { value: "*" }, "All sources"),
    ...payload.sources.map((source) => {
      const option = el("option", { value: source }, source);
      if (source === current) option.selected = true;
      return option;
    })
  );

  const start = payload.total === 0 ? 0 : payload.offset + 1;
  const end = Math.min(payload.offset + PAGE, payload.total);
  document.getElementById("page-info").textContent =
    `${start}–${end} of ${payload.total}`;

  const body = document.getElementById("postings-body");
  document.getElementById("postings-empty").hidden = payload.rows.length > 0;
  body.replaceChildren(
    ...payload.rows.map((row) => {
      const link = safeUrl(row.url);
      const tr = el("tr", { class: "row-link" },
        el("td", {}, row.title),
        el("td", {},
          el("div", {}, row.feed_key),
          el("div", { class: "mono muted" }, row.source)),
        el("td", {}, row.employment_type ?? "—"),
        el("td", {}, row.location ?? "—"),
        el("td", {},
          link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer",
            onclick: (event) => event.stopPropagation() }, "open") : "")
      );
      tr.addEventListener("click", () => showDetail(row));
      return tr;
    })
  );
}

async function showDetail(row) {
  document.getElementById("detail-title").textContent =
    `${row.title} · ${row.feed_key}`;
  const pane = document.getElementById("posting-json");
  pane.textContent = "Loading…";
  const detail = await api(`/api/postings-old/${row.result_id}/items/${row.item_index}`);
  pane.textContent = JSON.stringify(detail.payload, null, 2);
}

// Top-level await keeps the load event behind the first full render.
await load().catch((error) => {
  document.getElementById("postings-empty").hidden = false;
  document.getElementById("postings-empty").textContent =
    `Failed to load postings: ${error.message}`;
});
