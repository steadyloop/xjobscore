/* Companies awaiting review: the pending rows from /api/companies, corrected
   and verified in place. */

import { renderNav } from "/static/shared/nav.js";
import { api } from "/static/shared/api.js";
import { el, formatCount } from "/static/shared/format.js";
import { guessDomain, links, toRow } from "/static/companies/model.mjs";

// Same keys and order as companies.FILTERS on the server.
const FILTERS = [
  ["queue", "To do"],
  ["unverified", "Unreviewed"],
  ["verified", "Verified"],
  ["rejected", "Rejected"],
  ["all", "All"],
];
const LIMIT = 200;

const state = { rows: [], counts: {}, total: 0, filter: "queue", query: "", error: "", loading: false };

const body = document.getElementById("body");
const empty = document.getElementById("empty");
const summary = document.getElementById("summary");

function host(url) {
  try { return new URL(url).host; } catch { return ""; }
}

/* A 409 carries {message, hint, owner?}; everything else is already a string. */
function message(error) {
  const detail = error?.detail;
  if (detail && typeof detail === "object") return detail.message || error.message;
  return detail || error?.message || "failed";
}

/* The company already holding the domain, when the server names one. Its
   presence is what turns a dead-end 409 into a one-click merge. */
function blocker(error) {
  const owner = error?.detail?.owner;
  return owner && owner.id != null ? owner : null;
}

function pills() {
  const counts = { ...state.counts, all: state.counts.total };
  document.getElementById("filter-pills").replaceChildren(
    ...FILTERS.map(([key, label]) => el("button", {
      class: "pill", type: "button", "aria-pressed": String(state.filter === key),
      onclick: () => { state.filter = key; load(); },
    }, counts[key] == null ? label : `${label} (${formatCount(counts[key])})`))
  );
  summary.textContent = state.error
    ? state.error
    : state.total > state.rows.length
      ? `first ${state.rows.length} of ${formatCount(state.total)}`
      : `${formatCount(state.total)} shown`;
  summary.classList.toggle("co-error", Boolean(state.error));
}

function linksCell(row) {
  return el("td", {}, el("div", { class: "t-links" },
    links(row).map((l) => el("a", { href: l.href, target: "_blank", rel: "noopener" },
      l.label, " ", el("span", { class: "t-host" }, host(l.href))))));
}

/* The verdict is the server's; keep the two write paths in one place so a
   failed request leaves the row exactly as the operator left it. */
async function write(row, path, payload) {
  const previous = row.state;
  row.busy = true;
  row.error = "";
  row.blocker = null;
  refresh(row);
  try {
    const updated = await api(`/api/companies/${row.id}${path}`, { body: payload });
    if (path === "/merge") {
      state.rows = state.rows.filter((r) => r !== row);
      row.node.remove();
      state.total = Math.max(0, state.total - 1);
      pills();
      return;
    }
    Object.assign(row, toRow(updated), { node: row.node });
    recount(previous, row.state, row);
  } catch (error) {
    row.error = message(error);
    row.blocker = blocker(error);
  }
  row.busy = false;
  refresh(row);
  pills();
}

/* Mirror the server's count definitions locally rather than re-fetching the
   whole page after every write, which would throw away the operator's place. */
function recount(previous, next, row) {
  if (previous === next) return;
  const counts = state.counts;
  counts[previous] = Math.max(0, (counts[previous] || 0) - 1);
  counts[next] = (counts[next] || 0) + 1;
  if (row.postings > 0) {
    const delta = (next === "unverified") - (previous === "unverified");
    counts.queue = Math.max(0, (counts.queue || 0) + delta);
  }
}

function rowNode(row) {
  // The domain guess lives in the placeholder: grey until typed over, and
  // committed only if Save is pressed with the field still empty.
  const domain = el("input", { type: "text", value: row.domain, disabled: row.busy,
    placeholder: guessDomain(row.name) || "example.com",
    oninput: (e) => { row.domain = e.target.value; } });
  const name = el("input", { type: "text", value: row.name, disabled: row.busy,
    placeholder: "Company name",
    oninput: (e) => {
      row.name = e.target.value;
      domain.placeholder = guessDomain(row.name) || "example.com";
    } });
  const logo = el("span", { class: "t-logo" },
    row.hasLogo
      ? el("img", { src: `/api/companies/${row.id}/logo?v=${row.logoStamp || 0}`,
                    alt: "", width: "24", height: "24" })
      : null,
    el("button", {
      class: "btn small", type: "button", disabled: row.busy,
      // The typed value wins over the saved one, so the button works before
      // Save -- which is the order an operator actually fills the row in.
      onclick: () => fetchLogo(row, domain.value.trim() || row.domain),
    }, row.hasLogo ? "Refetch" : "Logo"));

  const description = el("textarea", { rows: "2", disabled: row.busy,
    placeholder: "What the company does",
    oninput: (e) => { row.description = e.target.value; } }, row.description);

  const save = el("button", {
    class: "btn primary small", type: "button", disabled: row.busy,
    onclick: () => {
      if (!row.name.trim()) { name.focus(); return; }
      if (!row.domain.trim() && domain.placeholder !== "example.com") {
        row.domain = domain.placeholder;
      }
      write(row, "/verify", {
        name: row.name, domain: row.domain, description: row.description,
      });
    },
  }, row.state === "verified" ? "Re-save" : "Save");

  const reopen = row.state === "unverified";
  const secondary = el("button", {
    class: "btn small", type: "button", disabled: row.busy,
    onclick: () => write(row, "/review", { state: reopen ? "rejected" : "pending" }),
  }, reopen ? "Reject" : "Reopen");

  const extra = row.tokens.length - 1;
  const label = row.primary
    ? `${row.primary.ats}/${row.primary.token}`
    : row.name || `#${row.id}`;
  const meta = [
    `${formatCount(row.postings)} postings`,
    row.state === "unverified" ? null : row.state,
    row.title,
  ].filter(Boolean).join(" · ");

  return el("tr", { "data-state": row.state },
    el("td", {},
      el("span", { class: "t-token", title: row.tokens.map((t) => `${t.ats}/${t.token}`).join("\n") || null },
        label, extra > 0 ? el("span", { class: "t-more" }, ` +${extra}`) : null),
      el("span", { class: "t-meta" }, meta),
      row.error ? el("span", { class: "t-error" }, row.error) : null,
      row.blocker ? mergeOffer(row) : null),
    linksCell(row),
    el("td", {}, name),
    el("td", {}, domain, logo),
    el("td", {}, description),
    el("td", {}, el("div", { class: "t-actions" }, save, secondary)));
}

/* Fold this row into the company that blocked it. Destructive and one-way, so
   it asks -- and it names both sides, because the direction is the whole
   decision: the blocker survives, this row does not. */
function mergeOffer(row) {
  const owner = row.blocker;
  const postings = owner.active_postings || 0;
  return el("span", { class: "t-merge" },
    el("button", {
      class: "btn small", type: "button", disabled: row.busy,
      onclick: () => {
        const keep = `${owner.name}${owner.domain ? ` (${owner.domain})` : ""}`;
        if (!confirm(
          `Merge "${row.name}" into "${keep}"?\n\n` +
          `${row.tokens.length} token(s) and ${formatCount(row.postings)} posting(s) move across.\n` +
          `"${row.name}" is then deleted. This cannot be undone.`
        )) return;
        write(row, "/merge", { target_id: owner.id });
      },
    }, `Merge into ${owner.name}`),
    el("span", { class: "t-meta" },
      `keeps ${owner.name}`,
      postings ? ` · ${formatCount(postings)} postings` : "",
      owner.reviewed === "verified" ? " · verified" : ""));
}

/* Harvest one logo on demand. Not a write() call: it touches no verdict and
   must not move the row between filters, so it only repaints. */
async function fetchLogo(row, domain) {
  if (!domain) { row.error = "add a domain first"; refresh(row); return; }
  row.busy = true;
  row.error = "";
  refresh(row);
  try {
    const result = await api(`/api/companies/${row.id}/logo`, { body: { domain } });
    row.hasLogo = Boolean(result.has_logo);
    // Cache-bust: the URL is stable, so a refetch would otherwise redraw the
    // image the browser already has.
    row.logoStamp = Date.now();
    if (!row.hasLogo) row.error = `${domain} ships no usable icon`;
  } catch (error) {
    row.error = message(error);
  }
  row.busy = false;
  refresh(row);
}

function refresh(row) {
  const next = rowNode(row);
  row.node.replaceWith(next);
  row.node = next;
}

function render() {
  pills();
  for (const row of state.rows) row.node = rowNode(row);
  body.replaceChildren(...state.rows.map((row) => row.node));
  empty.hidden = state.rows.length > 0;
  empty.textContent = state.loading ? "Loading…" : state.error || "Nothing to review.";
}

async function load() {
  state.loading = true;
  state.error = "";
  render();
  const query = new URLSearchParams({
    filter: state.filter, search: state.query, limit: String(LIMIT),
  });
  try {
    const data = await api(`/api/companies?${query}`);
    state.counts = data.counts || {};
    state.total = data.total || 0;
    state.rows = (data.rows || []).map(toRow);
  } catch (error) {
    state.error = message(error);
    state.counts = {};
    state.total = 0;
    state.rows = [];
  }
  state.loading = false;
  render();
}

let typing;
renderNav("Companies");
document.getElementById("search").addEventListener("input", (e) => {
  state.query = e.target.value;
  clearTimeout(typing);
  typing = setTimeout(load, 250);
});
document.getElementById("reload").addEventListener("click", load);
load();
