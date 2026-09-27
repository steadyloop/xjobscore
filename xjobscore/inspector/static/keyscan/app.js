/* Keyscan page: keyscan's own SQLite, read live.
 *
 * Reads aside, the page can do three things: start a run, cancel one, and
 * import what a run found. None of them make this page the crawl's owner --
 * a started run is detached and outlives the inspector, and cancel is a row
 * write the run itself polls. See keyscan_control.py.
 *
 * Plain text over chrome: the candidates table names each status, the feed
 * compresses it to a glyph, evidence over decoration.
 */

import { api, ApiError } from "/static/shared/api.js";
import { el } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

// While a run is live the two feeds are the point of the page; once it stops
// nothing changes until someone starts another, so back the poll right off.
const POLL_ACTIVE_MS = 4000;
const POLL_IDLE_MS = 30000;
const LOG_TAIL = 200;

// How many rows one page of the candidates table fetches. The table is
// unbounded -- tens of thousands of rows -- so it pages rather than trying to
// put every row in the DOM at once.
const PAGE = 200;

const state = {
  loaded: [],
  total: 0,
  runId: null,
  running: false,
  // Lines already consumed from run.jsonl, so each poll asks only for what is
  // new rather than re-reading a log that grows for the whole run.
  logOffset: 0,
  logEntries: [],
  timer: null,
  // Real Common Crawl snapshots, fetched once: id + the collection API's own
  // coverage window, newest first. What a run actually scans, not a typed
  // date range that may or may not land on a real boundary.
  crawls: [],
  selectedCrawls: new Set(),
  // "live" or "cache" -- the backend falls back to its last successful fetch
  // when Common Crawl's own index is unreachable, so the picker still has
  // something to offer instead of going empty.
  crawlsSource: null,
  crawlsFetchedAt: null,
};

const VERDICT_MARK = { live: "✓", dead: "✗", unknown: "·" };
const VERDICT_TITLE = {
  live: "board answered",
  dead: "board is gone",
  unknown: "not probed yet",
};
const RUN_CLASS = {
  running: "c-acc", completed: "c-ok", interrupted: "c-warn",
  cancelled: "c-warn", failed: "c-err",
};

/* Helpers ------------------------------------------------------------------ */

function clock(iso) {
  if (!iso) return "--/-- --:--";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "--/-- --:--";
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${pad(parsed.getMonth() + 1)}/${pad(parsed.getDate())}`;
  return `${date} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function ago(iso) {
  if (!iso) return null;
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(seconds)) return null;
  for (const [unit, size] of [["d", 86400], ["h", 3600], ["m", 60]]) {
    if (seconds >= size) return `${Math.floor(seconds / size)}${unit}`;
  }
  return `${Math.max(0, Math.floor(seconds))}s`;
}

function duration(from, to) {
  if (!from) return "—";
  const end = to ? new Date(to).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(from).getTime()) / 1000));
  const [h, m, s] = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60];
  if (h) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function count(value) {
  return typeof value === "number" ? value.toLocaleString() : "·";
}

/* The feed pairs its verdict with the "+" mark, where only a glyph is narrow
   enough to read as one unit; the table has the width to say the word. Even
   there `unknown` stays a dot -- it is by far the most common value and it
   asserts nothing, so it recedes rather than shouting the loudest. */
function verdict(status, { text = false } = {}) {
  const key = VERDICT_MARK[status] ? status : "unknown";
  return el(
    "span",
    {
      class: "ks-verdict",
      "data-verdict": key,
      "data-form": text ? "text" : "mark",
      title: VERDICT_TITLE[key],
    },
    text && key !== "unknown" ? key : VERDICT_MARK[key]
  );
}

function note(message, level = "info") {
  const banner = document.getElementById("page-note");
  banner.textContent = message;
  banner.dataset.level = level;
  banner.hidden = !message;
}

/* Latest run --------------------------------------------------------------- */

function renderRun(run) {
  const box = document.getElementById("run-box");
  if (!run) {
    box.hidden = true;
    state.runId = null;
    state.running = false;
    return;
  }
  box.hidden = false;
  const config = run.config ?? {};
  const checkpoint = run.checkpoint ?? {};
  const counts = run.counts ?? {};
  const methods = (run.methods ?? []).join(",") || "—";
  // Three config shapes can show up here: this run's own explicit snapshot
  // picks, an older run's typed date range, or a legacy newest-N run -- all
  // still readable from history even though the picker only writes the first.
  const scope = config.crawl_ids?.length
    ? `${config.crawl_ids.length} snapshot${config.crawl_ids.length === 1 ? "" : "s"}`
    : config.date_from
    ? `${config.date_from}..${config.date_to ?? "?"}`
    : `${config.crawls ?? "?"} snapshots`;

  document.getElementById("run-id").textContent = `run ${String(run.id).slice(0, 8)}`;
  const stateEl = document.getElementById("run-state");
  stateEl.textContent = `● ${run.status}`;
  stateEl.className = RUN_CLASS[run.status] ?? "c-mut";
  document.getElementById("run-meta").textContent =
    `${methods} · ${scope} · ${duration(run.started_at, run.finished_at)}`;
  document.getElementById("run-phase").textContent = run.phase ? `phase ${run.phase}` : "";

  // The only progress keyscan records is the current crawl's file counts;
  // with none yet a bar would be a guess, so it is hidden rather than faked.
  const { files, total_files: total } = checkpoint;
  const bar = document.getElementById("run-progress");
  // Only while running: a finished run's checkpoint is whatever the last
  // crawl left behind, which would read as 100% even on a failure.
  if (run.status === "running"
      && typeof files === "number" && typeof total === "number" && total > 0) {
    const pct = Math.min(100, Math.round((100 * files) / total));
    document.getElementById("run-fill").style.width = `${pct}%`;
    document.getElementById("run-pct").textContent = `${pct}%`;
    bar.hidden = false;
  } else {
    bar.hidden = true;
  }

  document.getElementById("run-counts").textContent = counts.found
    ? `+${count(counts.found)} candidates this run`
    : "";
  const errorEl = document.getElementById("run-error");
  errorEl.textContent = run.error ?? "";
  errorEl.hidden = !run.error;

  if (run.id !== state.runId) {
    // A different run than last poll: start its log from the top.
    state.runId = run.id;
    state.logOffset = 0;
    state.logEntries = [];
    document.getElementById("log-src").textContent =
      `runs/${String(run.id).slice(0, 8)}/run.jsonl`;
  }
  state.running = run.status === "running";
  // A `running` row whose process is gone is wreckage, not a run: say so
  // rather than offering a cancel that would do nothing useful.
  const stale = state.running && run.pid_alive === false;
  document.getElementById("cancel-btn").hidden = !state.running || stale;
  if (stale) stateEl.textContent = "● stale";
}

/* Tally -------------------------------------------------------------------- */

function renderTally(tally) {
  document.getElementById("tally").replaceChildren(
    el("b", {}, count(tally.found)), " found · ",
    el("span", { class: "c-ok" }, `${count(tally.live)} live`), " · ",
    el("span", { class: "c-err" }, `${count(tally.dead)} dead`), " · ",
    `${count(tally.unknown)} unknown · ${count(tally.imported)} imported`,
    // The import bridge takes no token list -- it writes every live row that
    // has never been imported -- so this is one action over that whole set,
    // not a selection.
    tally.pending
      ? el("span", {}, " · ", el("span", { class: "c-acc" }, `${count(tally.pending)} pending`), " ")
      : null,
    tally.pending
      ? el("button", { class: "ks-link", type: "button", id: "import-btn", onclick: runImport },
          "import them")
      : null
  );
}

/* Feeds -------------------------------------------------------------------- */

function renderFound(rows) {
  const lines = document.getElementById("activity-lines");
  if (!rows.length) {
    lines.replaceChildren(el("div", { class: "ks-empty" }, "no keys discovered yet"));
    return;
  }
  // Size the key and urls columns to what's actually on screen right now, so
  // a page of short keys doesn't leave "urls" stranded out at the pane's far
  // edge -- the gap tracks the content instead of the container width.
  const keyWidth = Math.max(...rows.map((row) => `${row.ats}/${row.key}`.length));
  const urlsText = (row) =>
    row.url_count == null ? "" : row.url_count === 1 ? "1 url" : `${count(row.url_count)} urls`;
  const urlsWidth = Math.max(0, ...rows.map((row) => urlsText(row).length));
  lines.replaceChildren(
    ...rows.map((row) =>
      el(
        "div",
        { class: "ks-line" },
        el("time", {}, clock(row.discovered_at)),
        el("span", { class: "ks-mark", "data-kind": "found" }, "+"),
        verdict(row.status),
        el(
          "b",
          { style: `width:${keyWidth}ch`, title: `${row.ats}/${row.key}` },
          `${row.ats}/${row.key}`
        ),
        el("span", { class: "ks-dim" }, row.method ?? ""),
        el("span", { class: "ks-count", style: `min-width:${urlsWidth}ch` }, urlsText(row))
      )
    )
  );
}

/* An HTTP status as a severity: 5xx is ours to worry about, 4xx is the host
   declining, anything below is just an answer. */
function httpLevel(code) {
  if (code >= 500) return "error";
  if (code >= 400) return "warn";
  return "ok";
}

/* Join parts with a separator, keeping element parts as elements -- Array.join
   would stringify the status code and lose the span carrying its colour. */
function interleave(parts, separator) {
  return parts.flatMap((part, index) => (index ? [separator, part] : [part]));
}

/* One run.jsonl entry, rendered from its own fields rather than from the CLI's
   flattened string: the function is its own column, and whatever numbers that
   event carries become the message and the right-hand metric. */
function logLine(raw) {
  // Details moved under `detail`; runs logged before that wrote them at the
  // top level, and `phase` became `function`. Reading a flattened view keeps
  // older runs -- still on disk, still openable from run history -- legible.
  const entry = { ...raw, ...(raw.detail ?? {}) };
  const fn = entry.function ?? entry.phase ?? "";
  const level = entry.level === "warning" ? "warn" : entry.level;
  const detail = [];
  if (entry.crawl) detail.push(entry.crawl);
  if (typeof entry.files === "number" && typeof entry.total_files === "number") {
    const pct = entry.total_files ? Math.round((100 * entry.files) / entry.total_files) : 0;
    detail.push(`files ${entry.files}/${entry.total_files} (${pct}%)`);
  }
  if (typeof entry.row_groups === "number") {
    detail.push(`${entry.row_groups} row groups`);
    if (typeof entry.bytes === "number") detail.push(`${Math.round(entry.bytes / 1e6)}MB`);
  }
  if (typeof entry.requests === "number") {
    detail.push(`${entry.requests} requests`);
    if (entry.failed) detail.push(`${entry.failed} failed`);
    if (typeof entry.median_ms === "number") detail.push(`median ${entry.median_ms}ms`);
  }
  // Verdict first, then the key it belongs to -- same reason as the status
  // code below: the outcome is what you scan for, and leading with it lines
  // every live/dead up at one x instead of hiding them behind ragged keys.
  if (entry.verdict) detail.push(entry.verdict);
  if (entry.token) detail.push(`${entry.ats}/${entry.token}`);
  if (typeof entry.candidates === "number") detail.push(`+${entry.candidates} candidates`);
  if (typeof entry.vendors === "number") detail.push(`${entry.vendors} vendors`);
  if (typeof entry.validated === "number") detail.push(`${entry.validated} done`);
  if (typeof entry.queued === "number") detail.push(`${entry.queued} queued`);
  // Status first, then the host it came from: the code is the fact you scan
  // for, and leading with it puts every code at the same x down the pane.
  if (typeof entry.status_code === "number") {
    detail.push(el(
      "span",
      { class: "ks-code", "data-level": httpLevel(entry.status_code) },
      String(entry.status_code)
    ));
  }
  if (entry.host) detail.push(entry.host);
  if (entry.error) detail.push(String(entry.error).replace(/\s+/g, " ").slice(0, 90));

  const ms = entry.elapsed_ms ?? entry.took_ms;
  const right = typeof ms === "number"
    ? (ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 60000 ? 0 : 1)}s` : `${ms}ms`)
    : "";

  // The colour rides on whatever names the outcome. A line carrying a status
  // code says it there; one without -- a failed scan, a refusal -- says it on
  // the message. Never on the function: which function ran is not news, and
  // colouring it painted whole screens of `crawl` for one bad request.
  const parts = detail.length ? interleave(detail, " · ") : [entry.message];
  return el(
    "div",
    { class: "ks-line" },
    el("time", {}, clock(entry.at)),
    el("span", { class: "ks-phase" }, fn),
    el(
      "span",
      { class: "ks-msg", "data-level": typeof entry.status_code === "number" ? null : level },
      ...parts
    ),
    right ? el("span", { class: "ks-urls" }, right) : null
  );
}

function renderLog() {
  const lines = document.getElementById("run-log-lines");
  if (!state.logEntries.length) {
    lines.replaceChildren(el("div", { class: "ks-empty" }, "no log for this run"));
    return;
  }
  // Stay pinned to the tail unless the reader has scrolled up to look at
  // something, which is the one case where jumping would be hostile.
  const stuck = lines.scrollTop + lines.clientHeight >= lines.scrollHeight - 24;
  lines.replaceChildren(...state.logEntries.map(logLine));
  if (stuck) lines.scrollTop = lines.scrollHeight;
}

/* Candidates --------------------------------------------------------------- */

/* Whether a candidate is really in PostgreSQL, checked against PostgreSQL
   rather than trusting keyscan's own imported_at. `null` means the check
   could not run. */
function inPostgres(row) {
  if (row.in_postgres === null || row.in_postgres === undefined) {
    return el("span", { class: "c-mut", title: "postgres unreachable" }, "?");
  }
  return row.in_postgres
    ? el("span", { class: "c-ok", title: "in the inventory" }, "imported")
    : el("span", { class: "c-err", title: "not imported yet" }, "unimported");
}

/* The table carries no header row, so each count says its own unit -- kept
   as two elements, not one string, so the unit can be muted a shade darker
   than the number. That contrast is what marks the boundary between them;
   without it "1 urls" reads as one blob instead of a value plus its label.
   Deliberately not `.num` (right-aligned): that class sizes the whole
   column to its widest row -- one "1,234 urls" pads every "1 urls" beside
   it -- which is the loose, far-apart look this replaces. */
function countOf(value, unit) {
  if (typeof value !== "number") return el("span", { class: "c-mut" }, "·");
  return el(
    "span",
    {},
    value.toLocaleString(),
    el("span", { class: "ks-unit" }, ` ${unit}`)
  );
}

function renderCandidates(rows) {
  document.getElementById("candidates-empty").hidden = rows.length > 0;
  document.getElementById("candidates-count").textContent =
    state.total ? count(state.total) : "";
  const more = document.getElementById("more-btn");
  more.hidden = rows.length >= state.total;
  more.textContent = `load ${count(Math.min(PAGE, state.total - rows.length))} more`;
  document.getElementById("candidates-body").replaceChildren(
    ...rows.map((row) =>
      el(
        "tr",
        {},
        el("td", {}, row.ats),
        el("td", { class: "mono" },
          el("a", {
            class: "ks-key",
            href: row.canonical_url,
            target: "_blank",
            rel: "noreferrer",
          }, row.key)),
        el("td", {}, verdict(row.status, { text: true })),
        el("td", { class: "ks-dim" }, row.method ?? ""),
        el("td", { class: "mono" }, countOf(row.url_count, "urls")),
        el("td", { class: "mono" }, countOf(row.job_count, "jobs")),
        el("td", { class: "mono" }, inPostgres(row))
      )
    )
  );
}

function renderRuns(rows) {
  document.getElementById("history-body").replaceChildren(
    ...rows.map((row) =>
      el(
        "tr",
        {},
        el("td", { class: "mono" }, String(row.id).slice(0, 8)),
        el("td", { class: "ks-dim" }, (row.methods ?? []).join(",")),
        el("td", { class: "mono" },
          el("span", { class: RUN_CLASS[row.status] ?? "" }, row.status)),
        el("td", { class: "num mono" }, count(row.counts?.found)),
        el("td", { class: "num mono" }, count(row.counts?.live)),
        el("td", { class: "num mono" }, count(row.counts?.dead)),
        el("td", { class: "ks-dim" }, ago(row.started_at) ?? "·"),
        el("td", { class: "mono" }, duration(row.started_at, row.finished_at))
      )
    )
  );
}

/* Loading ------------------------------------------------------------------ */

// Filter control -> query parameter. One list, so the URL, the request and
// the listeners cannot drift apart.
const FILTER_PARAMS = [
  ["vendor-filter", "source"],
  ["validation-filter", "status"],
  ["imported-filter", "imported"],
  ["search", "q"],
];

function filterValues() {
  const values = {};
  for (const [id, param] of FILTER_PARAMS) {
    const value = document.getElementById(id).value.trim();
    if (value) values[param] = value;
  }
  return values;
}

function syncUrl() {
  const params = new URLSearchParams(filterValues());
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

async function loadCandidates({ append = false } = {}) {
  const params = new URLSearchParams({
    ...filterValues(),
    limit: String(PAGE),
    offset: String(append ? state.loaded.length : 0),
  });
  const body = await api(`/api/keyscan/candidates?${params}`);
  state.total = body.total ?? 0;
  state.loaded = append
    ? [...state.loaded, ...(body.candidates ?? [])]
    : (body.candidates ?? []);
  renderCandidates(state.loaded);
}

async function loadLog() {
  if (!state.runId) {
    state.logEntries = [];
    renderLog();
    return;
  }
  const body = await api(
    `/api/keyscan/runs/${encodeURIComponent(state.runId)}/log` +
    `?offset=${state.logOffset}&limit=${LOG_TAIL}`
  );
  if (body.entries?.length) {
    state.logEntries = [...state.logEntries, ...body.entries].slice(-LOG_TAIL);
  }
  state.logOffset = body.offset ?? state.logOffset;
  renderLog();
}

async function section(render) {
  // Each part of the page stands on its own: a query that fails leaves its
  // own section empty and reports itself, rather than blanking the page.
  try {
    await render();
    return null;
  } catch (error) {
    return error;
  }
}

async function poll() {
  try {
    const failures = (
      await Promise.all([
        section(async () => renderRun((await api("/api/keyscan/status")).run)),
        section(async () => renderTally(await api("/api/keyscan/tally"))),
        section(async () =>
          renderFound((await api("/api/keyscan/found?limit=40")).found ?? [])),
        section(async () =>
          renderRuns((await api("/api/keyscan/runs?limit=15")).runs ?? [])),
        section(loadLog),
        section(loadCandidates),
      ])
    ).filter(Boolean);
    if (failures.length) throw failures[0];
    note("");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      // Never scanned on this machine: nothing is broken, there is just
      // nothing to show yet.
      note(`${error.detail} — run a scan from the CLI to populate it.`);
    } else {
      note(`could not read keyscan: ${error.message}`, "error");
    }
  } finally {
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, state.running ? POLL_ACTIVE_MS : POLL_IDLE_MS);
  }
}

async function runImport() {
  const button = document.getElementById("import-btn");
  if (button) button.disabled = true;
  note("importing…");
  try {
    const result = await api("/api/keyscan/import", { method: "POST" });
    // A skipped row is not a failure: it has no company match yet, and every
    // future import retries it.
    note(`imported ${result.written}, skipped ${result.skipped} with no company match`);
    await poll();
  } catch (error) {
    note(`import failed: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

/* A <select> is as wide as its longest option; on a command line it should be
   as wide as the value it is showing. */
function fitSelect(select) {
  const label = select.options[select.selectedIndex]?.text ?? "";
  select.style.width = `${Math.max(3, label.length) + 1}ch`;
}

/* Snapshot picker --------------------------------------------------------- */

function crawlsButtonLabel() {
  const n = state.selectedCrawls.size;
  if (!state.crawls.length) return "unavailable";
  if (n === 0) return "select snapshots…";
  if (n === 1) return [...state.selectedCrawls][0];
  return `${n} snapshots`;
}

function renderCrawlsPanel() {
  const panel = document.getElementById("crawls-panel");
  const rows = [];
  // A cached snapshot list is served silently: it is days-stale at worst and
  // still picks a valid crawl, so the fallback is not worth a warning.
  // `state.crawlsSource` / `crawlsFetchedAt` still record it for debugging.
  if (!state.crawls.length) {
    rows.push(el("div", { class: "ks-dim" }, "no snapshots available"));
  } else {
    rows.push(
      ...state.crawls.map((crawl) =>
        el(
          "label",
          {},
          el("input", {
            type: "checkbox",
            checked: state.selectedCrawls.has(crawl.id),
            onchange: (event) => {
              if (event.target.checked) state.selectedCrawls.add(crawl.id);
              else state.selectedCrawls.delete(crawl.id);
              document.getElementById("crawls-btn").textContent = crawlsButtonLabel();
            },
          }),
          crawl.id,
          el("span", { class: "ks-dim" }, `${crawl.from}..${crawl.to}`)
        )
      )
    );
  }
  panel.replaceChildren(...rows);
  document.getElementById("crawls-btn").textContent = crawlsButtonLabel();
}

async function loadCrawls() {
  try {
    const body = await api("/api/keyscan/crawls");
    state.crawls = body.crawls ?? [];
    state.crawlsSource = body.source ?? null;
    state.crawlsFetchedAt = body.fetched_at ?? null;
    // A sane one-click default: the single newest snapshot, not every one of
    // them -- selecting all of them is one more click away, not the default.
    if (state.crawls.length) state.selectedCrawls.add(state.crawls[0].id);
  } catch (error) {
    state.crawls = [];
    note(`could not list Common Crawl snapshots: ${error.message}`, "error");
  }
  renderCrawlsPanel();
}

function toggleCrawlsPanel(force) {
  const button = document.getElementById("crawls-btn");
  const panel = document.getElementById("crawls-panel");
  const open = force ?? panel.hidden;
  panel.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
}

/* Run launch ---------------------------------------------------------------- */

async function startRun() {
  if (!state.selectedCrawls.size) {
    note("pick at least one Common Crawl snapshot to scan", "error");
    return;
  }
  const button = document.getElementById("start-run-btn");
  button.disabled = true;
  note("starting…");
  try {
    const result = await api("/api/keyscan/run", {
      method: "POST",
      body: {
        crawl_ids: [...state.selectedCrawls].join(","),
        sources: document.getElementById("sources").value || null,
      },
    });
    // keyscan writes its own runs row a moment after the process starts, so
    // the run box appears on a poll rather than from this response.
    note(`started (pid ${result.pid}) — ${result.argv.join(" ")}`);
    setTimeout(poll, 800);
  } catch (error) {
    note(`could not start: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function cancelRun() {
  if (!state.runId) return;
  const button = document.getElementById("cancel-btn");
  button.disabled = true;
  note("cancelling — the run stops at its next checkpoint…");
  try {
    await api(`/api/keyscan/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST" });
    await poll();
  } catch (error) {
    note(`could not cancel: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}


function init() {
  renderNav("Keyscan");

  const sources = document.getElementById("sources");
  fitSelect(sources);
  sources.addEventListener("change", () => fitSelect(sources));

  document.getElementById("start-run-btn").addEventListener("click", startRun);
  document.getElementById("cancel-btn").addEventListener("click", cancelRun);

  document.getElementById("crawls-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleCrawlsPanel();
  });
  document.addEventListener("click", (event) => {
    if (!document.getElementById("crawls-field").contains(event.target)) toggleCrawlsPanel(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") toggleCrawlsPanel(false);
  });
  loadCrawls();

  let debounce = null;
  document.getElementById("more-btn").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      await loadCandidates({ append: true });
    } finally {
      event.target.disabled = false;
    }
  });

  // Filters are readable from the URL, so a filtered view is shareable and
  // survives a reload.
  const initial = new URLSearchParams(location.search);
  for (const [id, param] of FILTER_PARAMS) {
    const value = initial.get(param);
    if (value !== null) document.getElementById(id).value = value;
  }

  // `change` is the event a <select> is guaranteed to fire; the search box
  // wants `input` so it filters as you type.
  for (const [id] of FILTER_PARAMS) {
    const element = document.getElementById(id);
    const event = element.tagName === "SELECT" ? "change" : "input";
    element.addEventListener(event, () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        syncUrl();
        loadCandidates().catch((error) => note(error.message, "error"));
      }, element.tagName === "SELECT" ? 0 : 200);
    });
  }

  document.getElementById("foot").textContent =
    "keyscan/work/keyscan.sqlite · runs are detached: they survive an inspector restart";

  poll();
}

init();
