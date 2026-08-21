/* Query: ad-hoc read-only SQL over the pipeline database.
 *
 * Safety is entirely server-side (see query.py) — this page just gives an
 * admin a place to type SQL, a schema to remind them what's there, and
 * somewhere to land when a query is wrong. Nothing here is a security
 * boundary: it exists to make the common cases fast (a table name, a past
 * query, a canned starter) not to second-guess the backend.
 */

import { api, isDegraded } from "/static/shared/api.js";
import { el } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Query");

const SQL_KEY = "inspector-query-sql";
const HISTORY_KEY = "inspector-query-history";
const HISTORY_LIMIT = 20;

const SNIPPETS = [
  {
    label: "Row counts per table",
    sql: "select relname as table_name, n_live_tup as approx_rows\nfrom pg_stat_user_tables\norder by n_live_tup desc;",
  },
  {
    label: "Newest postings",
    sql: "select p.id, c.name as company, p.title, p.source, p.first_seen_at\nfrom job_posting p\njoin company c on c.id = p.company_id\norder by p.first_seen_at desc\nlimit 50;",
  },
  {
    label: "Postings per source",
    sql: "select source, count(*) as postings\nfrom job_posting\ngroup by source\norder by postings desc;",
  },
  {
    label: "Poll errors, last 24h",
    sql: "select po.polled_at, f.source, f.key, po.status\nfrom poll_observation po\njoin feed f on f.id = po.feed_id\nwhere po.status = 'error' and po.polled_at > now() - interval '24 hours'\norder by po.polled_at desc;",
  },
  {
    label: "Postings missing enrichment",
    sql: "select id, source, title\nfrom job_posting\nwhere job_type IS NULL\nlimit 100;",
  },
];

const editor = document.getElementById("sql-editor");
const runBtn = document.getElementById("run-btn");
const runStatus = document.getElementById("run-status");
const degradedBanner = document.getElementById("degraded-banner");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");
const resultsTable = document.getElementById("results-table");
const resultsHead = document.getElementById("results-head");
const resultsBody = document.getElementById("results-body");
const resultsEmpty = document.getElementById("results-empty");
const resultsFoot = document.getElementById("results-foot");
const rowDetail = document.getElementById("row-detail");
const rowDetailJson = document.getElementById("row-detail-json");
const schemaList = document.getElementById("schema-list");
const schemaFilter = document.getElementById("schema-filter");
const historyList = document.getElementById("history-list");
const historyEmpty = document.getElementById("history-empty");
const snippetSelect = document.getElementById("snippet-select");

const state = {
  running: false,
  lastResult: null,
  schema: null,
};

/* ------------------------------------------------------------ persistence */

editor.value = localStorage.getItem(SQL_KEY) ?? "";

editor.addEventListener("input", () => {
  localStorage.setItem(SQL_KEY, editor.value);
});

function loadHistory() {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((entry) => typeof entry === "string") : [];
  } catch {
    return [];
  }
}

function pushHistory(sql) {
  const trimmed = sql.trim();
  if (!trimmed) return;
  const history = [trimmed, ...loadHistory().filter((entry) => entry !== trimmed)].slice(
    0,
    HISTORY_LIMIT
  );
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  renderHistory();
}

function renderHistory() {
  const history = loadHistory();
  historyEmpty.hidden = history.length > 0;
  historyList.replaceChildren(
    ...history.map((sql) =>
      el(
        "button",
        { type: "button", title: sql, onclick: () => loadSql(sql) },
        sql.replace(/\s+/g, " ")
      )
    )
  );
}

function loadSql(sql) {
  editor.value = sql;
  localStorage.setItem(SQL_KEY, sql);
  editor.focus();
}

/* ------------------------------------------------------------------ editor */

editor.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    runQuery();
    return;
  }
  if (event.key === "Tab") {
    event.preventDefault();
    const { selectionStart, selectionEnd, value } = editor;
    editor.value = `${value.slice(0, selectionStart)}  ${value.slice(selectionEnd)}`;
    editor.selectionStart = editor.selectionEnd = selectionStart + 2;
  }
});

function insertAtCursor(text) {
  const { selectionStart, selectionEnd, value } = editor;
  const needsSpace = selectionStart > 0 && !/\s$/.test(value.slice(0, selectionStart));
  const insert = needsSpace ? ` ${text}` : text;
  editor.value = value.slice(0, selectionStart) + insert + value.slice(selectionEnd);
  const cursor = selectionStart + insert.length;
  editor.focus();
  editor.selectionStart = editor.selectionEnd = cursor;
  localStorage.setItem(SQL_KEY, editor.value);
}

/* ------------------------------------------------------------------ run */

runBtn.addEventListener("click", runQuery);

async function runQuery() {
  const sql = editor.value.trim();
  if (!sql || state.running) return;

  state.running = true;
  runBtn.disabled = true;
  runStatus.textContent = "Running…";
  errorBanner.hidden = true;
  hideRowDetail();

  try {
    const result = await api("/api/query", { body: { sql } });
    state.lastResult = result;
    renderResults(result);
    pushHistory(sql);
    const rowWord = result.row_count === 1 ? "row" : "rows";
    runStatus.textContent = `${result.row_count} ${rowWord} · ${Math.round(result.elapsed_ms)} ms`;
  } catch (error) {
    runStatus.textContent = "";
    if (isDegraded(error)) {
      showDegraded();
    } else {
      showError(error);
    }
  } finally {
    state.running = false;
    runBtn.disabled = degradedBanner.hidden ? false : true;
  }
}

function showError(error) {
  const detail = error.detail;
  let message = typeof detail === "string" ? detail : detail?.message ?? error.message;
  if (detail && typeof detail === "object" && detail.position) {
    message += ` (at character ${detail.position})`;
  }
  errorText.textContent = message;
  errorBanner.hidden = false;
}

function showDegraded() {
  degradedBanner.hidden = false;
  runBtn.disabled = true;
}

/* -------------------------------------------------------------- results */

function renderResults(result) {
  hideRowDetail();
  const hasRows = result.columns.length > 0;
  resultsTable.hidden = !hasRows;
  resultsEmpty.hidden = hasRows;
  resultsEmpty.textContent = hasRows ? "" : "Query returned no columns.";

  resultsHead.replaceChildren(
    ...result.columns.map((name, index) =>
      el(
        "th",
        { class: isNumericType(result.types[index]) ? "num" : null, title: result.types[index] },
        name
      )
    )
  );

  resultsBody.replaceChildren(
    ...result.rows.map((row, rowIndex) =>
      el(
        "tr",
        { class: "row-link", onclick: () => showRowDetail(result, row) },
        row.map((value, colIndex) =>
          el(
            "td",
            { class: isNumericType(result.types[colIndex]) ? "num" : null },
            renderCell(value)
          )
        )
      )
    )
  );

  if (result.truncated) {
    resultsFoot.hidden = false;
    resultsFoot.textContent = `Showing the first ${result.max_rows} rows — add LIMIT/OFFSET to page further.`;
  } else {
    resultsFoot.hidden = true;
  }
}

function renderCell(value) {
  if (value === null) {
    return el("span", { class: "query-cell null", title: "NULL" }, "NULL");
  }
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return el("span", { class: "query-cell", title: text }, text);
}

function isNumericType(type) {
  return /int|numeric|float|double|real|money|oid/.test(type ?? "");
}

function showRowDetail(result, row) {
  const record = Object.fromEntries(result.columns.map((name, index) => [name, row[index]]));
  rowDetailJson.textContent = JSON.stringify(record, null, 2);
  rowDetail.hidden = false;
}

function hideRowDetail() {
  rowDetail.hidden = true;
  rowDetailJson.textContent = "";
}

document.getElementById("row-detail-close").addEventListener("click", hideRowDetail);

/* --------------------------------------------------------------- schema */

async function loadSchema() {
  try {
    const result = await api("/api/query/schema");
    state.schema = result.tables ?? [];
    renderSchema(state.schema);
  } catch (error) {
    if (isDegraded(error)) showDegraded();
  }
}

function renderSchema(tables) {
  const filter = schemaFilter.value.trim().toLowerCase();
  const visible = filter ? tables.filter((t) => t.name.includes(filter)) : tables;

  if (!visible.length) {
    schemaList.replaceChildren(
      el("p", { class: "muted" }, filter ? "No tables match." : "No tables found.")
    );
    return;
  }

  schemaList.replaceChildren(
    ...visible.map((table) =>
      el(
        "details",
        { class: "query-schema-table" },
        el(
          "summary",
          {},
          el(
            "span",
            { class: "table-name", title: table.name, onclick: (e) => (e.preventDefault(), insertAtCursor(table.name)) },
            table.name
          ),
          el("span", { class: "row-count" }, formatApproxRows(table.approx_rows))
        ),
        el(
          "div",
          { class: "query-schema-columns" },
          table.columns.map((col) =>
            el(
              "button",
              { type: "button", onclick: () => insertAtCursor(col.name) },
              el("span", {}, col.name),
              el("span", { class: "col-type" }, col.type)
            )
          )
        )
      )
    )
  );
}

function formatApproxRows(value) {
  if (value == null) return "";
  return new Intl.NumberFormat(undefined, { notation: "compact" }).format(value);
}

schemaFilter.addEventListener("input", () => {
  if (state.schema) renderSchema(state.schema);
});

/* -------------------------------------------------------------- snippets */

snippetSelect.append(
  ...SNIPPETS.map((snippet, index) => el("option", { value: index }, snippet.label))
);

snippetSelect.addEventListener("change", () => {
  const index = snippetSelect.value;
  if (index === "") return;
  loadSql(SNIPPETS[Number(index)].sql);
  snippetSelect.value = "";
});

/* ------------------------------------------------------------------ boot */

resultsTable.hidden = true;
renderHistory();
await loadSchema();
