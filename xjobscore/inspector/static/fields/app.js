/* Fields page: what an adapter's payload holds, and what it collapses to.
 *
 * Two requests with deliberately different costs, and the UI keeps that
 * distinction visible: the survey is a sample and is labelled approximate,
 * the value histogram is an exact full scan and is labelled exact. Blurring
 * the two would be the one way this page could mislead a recon pass.
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el, formatBytes, formatCount, formatMs } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Fields");

const SAMPLE_SIZES = [500, 2000, 5000, 20000];
const DEPTHS = [1, 2, 3, 4];

const state = {
  adapters: [],
  adapter: null,
  feed: "",
  sample: 2000,
  depth: 3,
  survey: null,
  path: null,
};

const adapterList = document.getElementById("adapter-list");
const adapterFilter = document.getElementById("adapter-filter");
const feedSelect = document.getElementById("feed-select");
const sampleSelect = document.getElementById("sample-select");
const depthSelect = document.getElementById("depth-select");
const pathFilter = document.getElementById("path-filter");
const unmappedOnly = document.getElementById("unmapped-only");
const surveyStatus = document.getElementById("survey-status");
const surveyTitle = document.getElementById("survey-title");
const surveyBadge = document.getElementById("survey-badge");
const surveyNotice = document.getElementById("survey-notice");
const surveyTable = document.getElementById("survey-table");
const surveyBody = document.getElementById("survey-body");
const surveyEmpty = document.getElementById("survey-empty");
const surveyFoot = document.getElementById("survey-foot");
const degradedBanner = document.getElementById("degraded-banner");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");
const detailPane = document.getElementById("detail-pane");
const detailPath = document.getElementById("detail-path");
const detailStats = document.getElementById("detail-stats");
const detailChain = document.getElementById("detail-chain");
const detailValues = document.getElementById("detail-values");
const detailEmpty = document.getElementById("detail-empty");
const detailExamples = document.getElementById("detail-examples");
const examplesTitle = document.getElementById("examples-title");
const examplesJson = document.getElementById("examples-json");
const layout = document.getElementById("fields-layout");

/* The detail pane is a grid column, so showing it has to widen the grid --
 * otherwise the survey holds a 400px gap open for a pane that is not there. */
function setDetail(open) {
  detailPane.hidden = !open;
  layout.classList.toggle("with-detail", open);
}

/* Recon findings get shared ("look at ashby's workplaceType"), so the whole
 * selection lives in the query string and a reload lands back on it. */
function syncUrl() {
  const params = new URLSearchParams();
  if (state.adapter) params.set("adapter", state.adapter);
  if (state.feed) params.set("feed", state.feed);
  if (state.path) params.set("path", state.path);
  if (state.sample !== 2000) params.set("sample", String(state.sample));
  if (state.depth !== 3) params.set("depth", String(state.depth));
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

function showError(error) {
  errorText.textContent = error?.message ?? String(error);
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  degradedBanner.hidden = true;
}

function handle(error) {
  if (isDegraded(error)) degradedBanner.hidden = false;
  else showError(error);
}

function percent(value) {
  if (value == null) return "—";
  if (value > 0 && value < 0.001) return "<0.1%";
  return `${(value * 100).toFixed(value >= 0.995 || value === 0 ? 0 : 1)}%`;
}

/* ---- adapters --------------------------------------------------------- */

function renderAdapters() {
  const needle = adapterFilter.value.trim().toLowerCase();
  const rows = state.adapters.filter((row) => row.adapter.includes(needle));
  adapterList.replaceChildren(
    ...rows.map((row) =>
      el(
        "button",
        {
          type: "button",
          class: "fields-adapter",
          "aria-current": row.adapter === state.adapter ? "true" : null,
          "data-surveyable": String(row.surveyable),
          title: `${formatCount(row.revisions)} revisions across ${formatCount(row.feeds)} feeds · ${formatBytes(row.payload_bytes)} of payload · kind ${row.kind}`,
          onclick: () => selectAdapter(row.adapter),
        },
        el("span", { class: "name mono" }, row.adapter),
        el("span", { class: "count num" }, formatCount(row.postings)),
        row.surveyable ? null : el("span", { class: "badge", title: `payload is a ${row.kind} document` }, row.kind)
      )
    )
  );
  if (!rows.length) {
    adapterList.replaceChildren(el("p", { class: "muted" }, "No adapter matches."));
  }
}

/* ---- survey ----------------------------------------------------------- */

function canonicalCell(mapped) {
  const fields = mapped?.fields ?? [];
  if (!fields.length) return el("span", { class: "muted" }, "—");
  const label = fields.join(", ");
  if (mapped.inherited) {
    return el(
      "span",
      {
        class: "fields-canon inherited",
        title: `Not mapped by name; carried inside "${mapped.inherited}" → ${label}`,
      },
      `↑ ${label}`
    );
  }
  return el("span", { class: "fields-canon" }, label);
}

function surveyRows() {
  const needle = pathFilter.value.trim().toLowerCase();
  return (state.survey?.paths ?? []).filter((row) => {
    if (needle && !row.path.toLowerCase().includes(needle)) return false;
    if (unmappedOnly.checked && (row.mapped?.fields ?? []).length) return false;
    return true;
  });
}

function renderSurvey() {
  const survey = state.survey;
  if (!survey) return;
  const rows = surveyRows();
  surveyBody.replaceChildren(
    ...rows.map((row) =>
      el(
        "tr",
        {
          class: "row-link",
          "aria-current": row.path === state.path ? "true" : null,
          // The sampled example belongs on the row, not in a column: it is
          // one value out of thousands, and the detail pane answers the same
          // question exactly.
          title: row.example == null ? row.path : `${row.path}\ne.g. ${row.example}`,
          onclick: () => loadValues(row.path),
        },
        el("td", { class: "mono fields-path" }, row.path),
        el("td", { class: "secondary" }, row.kinds.join(" | ") || "—"),
        el("td", { class: "num" }, percent(row.coverage)),
        el("td", { class: "num" }, percent(row.fill)),
        el("td", { class: "num" }, formatCount(row.distinct_values)),
        el("td", {}, canonicalCell(row.mapped))
      )
    )
  );
  surveyTable.hidden = rows.length === 0;
  surveyEmpty.hidden = rows.length > 0;
  surveyEmpty.textContent = survey.paths.length
    ? "No path matches the current filter."
    : "No JSON fields found in the sampled payloads.";
  surveyFoot.hidden = false;
  surveyFoot.textContent =
    `${formatCount(rows.length)} of ${formatCount(survey.paths.length)} paths · ` +
    `sampled ${formatCount(survey.sampled)} payloads · depth ${survey.depth} · ${formatMs(survey.elapsed_ms)}`;
}

async function loadSurvey() {
  const adapter = state.adapters.find((row) => row.adapter === state.adapter);
  surveyTitle.textContent = state.adapter ?? "Field survey";
  surveyBadge.hidden = false;
  surveyBadge.textContent = "approximate";
  surveyNotice.hidden = true;

  if (adapter && !adapter.surveyable) {
    // An xml/html payload is a document in a string: there is no JSON tree to
    // walk, and an empty table would read as "this adapter has no fields".
    state.survey = null;
    surveyTable.hidden = true;
    surveyFoot.hidden = true;
    surveyBadge.hidden = true;
    surveyEmpty.hidden = true;
    surveyNotice.hidden = false;
    surveyNotice.replaceChildren(
      el("strong", {}, `${adapter.adapter} payloads are ${adapter.kind} documents, not JSON objects.`),
      el(
        "p",
        {},
        "There is no field tree to survey. The configured extraction paths are " +
          "listed in the panel on the right; use the Feed page's Parsed tab to see " +
          "one document run through its parser."
      )
    );
    await showMappingOnly(adapter.adapter);
    return;
  }

  surveyStatus.textContent = "surveying…";
  try {
    const params = new URLSearchParams({
      adapter: state.adapter,
      sample: String(state.sample),
      depth: String(state.depth),
    });
    if (state.feed) params.set("feed", state.feed);
    state.survey = await api(`/api/fields/survey?${params}`);
    clearError();
    renderSurvey();
    surveyStatus.textContent = `${formatMs(state.survey.elapsed_ms)} · sample of ${formatCount(state.survey.sampled)}`;
  } catch (error) {
    state.survey = null;
    surveyTable.hidden = true;
    surveyFoot.hidden = true;
    surveyStatus.textContent = "";
    handle(error);
  }
}

async function loadFeeds() {
  feedSelect.disabled = true;
  feedSelect.replaceChildren(el("option", { value: "" }, "All feeds"));
  try {
    const result = await api(`/api/fields/feeds?adapter=${encodeURIComponent(state.adapter)}`);
    feedSelect.replaceChildren(
      el("option", { value: "" }, `All feeds (${formatCount(result.feeds.length)})`),
      ...result.feeds.map((row) =>
        el("option", { value: row.feed }, `${row.feed} — ${formatCount(row.postings)}`)
      )
    );
    feedSelect.disabled = false;
  } catch (error) {
    handle(error);
  }
}

async function selectAdapter(adapter) {
  state.adapter = adapter;
  state.feed = "";
  state.path = null;
  setDetail(false);
  pathFilter.disabled = false;
  renderAdapters();
  syncUrl();
  await Promise.all([loadFeeds(), loadSurvey()]);
}

/* ---- field detail ------------------------------------------------------ */

function chainBlock(chain) {
  const steps = [chain.field];
  if (chain.collapse) steps.push(chain.collapse);
  if (chain.target) steps.push(chain.target);
  return el(
    "div",
    { class: "fields-chain", "data-projected": String(chain.projected) },
    el(
      "div",
      { class: "fields-chain-steps" },
      steps.flatMap((step, index) => [
        index ? el("span", { class: "arrow" }, "→") : null,
        el("code", {}, step),
      ])
    ),
    chain.column
      ? el("p", { class: "secondary" }, `job_posting.${chain.column}`)
      : null,
    chain.domain
      ? el(
          "p",
          { class: "fields-domain" },
          "domain: ",
          chain.domain.map((value) => el("span", { class: "pill" }, value))
        )
      : null,
    chain.note ? el("p", { class: "muted" }, chain.note) : null
  );
}

function renderChains(result) {
  const mapped = result.mapped ?? { fields: [], inherited: null };
  const blocks = [];
  if (!mapped.fields.length) {
    blocks.push(
      el(
        "div",
        { class: "fields-chain", "data-projected": "false" },
        el("div", { class: "fields-chain-steps" }, el("code", {}, "(unmapped)")),
        el(
          "p",
          { class: "muted" },
          "No config expression reads this path. It exists in bronze and never " +
            "reaches job_posting."
        )
      )
    );
  } else {
    if (mapped.inherited) {
      blocks.push(
        el(
          "p",
          { class: "muted" },
          `Not read by name — carried inside "${mapped.inherited}".`
        )
      );
    }
    blocks.push(...(result.chains ?? []).map(chainBlock));
  }
  detailChain.replaceChildren(
    el("h2", { class: "fields-subhead" }, "Collapse chain"),
    ...blocks
  );
}

function collapseFor(result, value) {
  for (const table of Object.values(result.collapsed ?? {})) {
    if (Object.hasOwn(table, value)) return table[value];
  }
  return undefined;
}

function renderValues(result) {
  const top = result.values[0]?.count ?? 1;
  detailValues.replaceChildren(
    el("h2", { class: "fields-subhead" }, "Values"),
    ...result.values.map((entry) => {
      const label = entry.value === null ? "(absent)" : entry.value === "" ? "(empty)" : String(entry.value);
      const collapsed = collapseFor(result, entry.value);
      return el(
        "button",
        {
          type: "button",
          class: "fields-value",
          "data-null": String(entry.value === null || entry.value === ""),
          title: "Show example payloads",
          onclick: () => loadExamples(result.path, entry.value, label),
        },
        el("span", { class: "bar", style: `width:${Math.max(2, (entry.count / top) * 100)}%` }),
        el("span", { class: "label" }, label),
        collapsed === undefined
          ? null
          : el(
              "span",
              { class: "collapsed", "data-none": String(collapsed == null) },
              `→ ${collapsed ?? "none"}`
            ),
        el("span", { class: "count num" }, formatCount(entry.count)),
        el("span", { class: "share num secondary" }, percent(entry.share))
      );
    })
  );
}

async function loadValues(path) {
  state.path = path;
  renderSurvey();
  syncUrl();
  setDetail(true);
  detailExamples.hidden = true;
  detailPath.textContent = path;
  detailStats.replaceChildren();
  detailChain.replaceChildren();
  detailValues.replaceChildren();
  detailEmpty.hidden = false;
  detailEmpty.textContent = "Counting every posting…";

  try {
    const params = new URLSearchParams({ adapter: state.adapter, path });
    if (state.feed) params.set("feed", state.feed);
    const result = await api(`/api/fields/values?${params}`);
    clearError();
    detailEmpty.hidden = true;
    detailStats.replaceChildren(
      el("div", { class: "stat" }, el("div", { class: "label" }, "Scope"),
        el("div", { class: "value" }, "exact")),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Postings"),
        el("div", { class: "value" }, formatCount(result.postings))),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Occurrences"),
        el("div", { class: "value" }, formatCount(result.total))),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Distinct"),
        el("div", { class: "value" }, formatCount(result.distinct_values))),
      el("div", { class: "stat" }, el("div", { class: "label" }, "Took"),
        el("div", { class: "value" }, formatMs(result.elapsed_ms)))
    );
    renderChains(result);
    renderValues(result);
    if (result.truncated) {
      detailValues.append(
        el(
          "p",
          { class: "muted" },
          `Showing the top ${formatCount(result.limit)} of ${formatCount(result.distinct_values)} distinct values.`
        )
      );
    }
  } catch (error) {
    detailEmpty.hidden = false;
    detailEmpty.textContent = isDegraded(error)
      ? "PostgreSQL is unreachable."
      : error?.message ?? String(error);
    handle(error);
  }
}

async function showMappingOnly(adapter) {
  setDetail(true);
  detailPath.textContent = adapter;
  detailStats.replaceChildren();
  detailValues.replaceChildren();
  detailExamples.hidden = true;
  detailEmpty.hidden = true;
  try {
    const result = await api(`/api/fields/mapping?adapter=${encodeURIComponent(adapter)}`);
    const entries = Object.entries(result.paths);
    detailChain.replaceChildren(
      el("h2", { class: "fields-subhead" }, "Configured paths"),
      result.note ? el("p", { class: "muted" }, result.note) : null,
      entries.length
        ? el(
            "table",
            { class: "data" },
            el("tbody", {},
              entries.map(([path, fields]) =>
                el("tr", {},
                  el("td", { class: "mono" }, path),
                  el("td", { class: "secondary" }, fields.join(", "))
                )
              )
            )
          )
        : el("p", { class: "muted" }, "No introspectable paths.")
    );
  } catch (error) {
    handle(error);
  }
}

async function loadExamples(path, value, label) {
  detailExamples.hidden = false;
  examplesTitle.textContent = `Examples — ${label}`;
  examplesJson.textContent = "Loading…";
  try {
    const params = new URLSearchParams({ adapter: state.adapter, path });
    if (value !== null) params.set("value", value);
    if (state.feed) params.set("feed", state.feed);
    const result = await api(`/api/fields/examples?${params}`);
    examplesJson.textContent = result.examples.length
      ? JSON.stringify(result.examples, null, 2)
      : "No matching posting.";
  } catch (error) {
    examplesJson.textContent = error?.message ?? String(error);
  }
}

/* ---- wiring ------------------------------------------------------------ */

sampleSelect.replaceChildren(
  ...SAMPLE_SIZES.map((size) =>
    el("option", { value: String(size), selected: size === state.sample ? "" : null },
      `sample ${formatCount(size)}`)
  )
);
depthSelect.replaceChildren(
  ...DEPTHS.map((depth) =>
    el("option", { value: String(depth), selected: depth === state.depth ? "" : null },
      `depth ${depth}`)
  )
);

adapterFilter.addEventListener("input", renderAdapters);
pathFilter.addEventListener("input", renderSurvey);
unmappedOnly.addEventListener("change", renderSurvey);
feedSelect.addEventListener("change", () => {
  state.feed = feedSelect.value;
  state.path = null;
  setDetail(false);
  loadSurvey();
});
sampleSelect.addEventListener("change", () => {
  state.sample = Number(sampleSelect.value);
  syncUrl();
  loadSurvey();
});
depthSelect.addEventListener("change", () => {
  state.depth = Number(depthSelect.value);
  syncUrl();
  loadSurvey();
});
document.getElementById("detail-close").addEventListener("click", () => {
  setDetail(false);
  state.path = null;
  renderSurvey();
  syncUrl();
});
document.getElementById("examples-close").addEventListener("click", () => {
  detailExamples.hidden = true;
});

const initial = new URLSearchParams(location.search);
if (SAMPLE_SIZES.includes(Number(initial.get("sample")))) {
  state.sample = Number(initial.get("sample"));
  sampleSelect.value = String(state.sample);
}
if (DEPTHS.includes(Number(initial.get("depth")))) {
  state.depth = Number(initial.get("depth"));
  depthSelect.value = String(state.depth);
}

try {
  const result = await api("/api/fields/adapters");
  state.adapters = result.adapters;
  renderAdapters();
  const wanted = initial.get("adapter");
  if (wanted && state.adapters.some((row) => row.adapter === wanted)) {
    await selectAdapter(wanted);
    const feed = initial.get("feed");
    if (feed && [...feedSelect.options].some((option) => option.value === feed)) {
      feedSelect.value = feed;
      state.feed = feed;
      await loadSurvey();
    }
    const path = initial.get("path");
    if (path) await loadValues(path);
  }
} catch (error) {
  handle(error);
  adapterList.replaceChildren(el("p", { class: "muted" }, "Adapters unavailable."));
}
