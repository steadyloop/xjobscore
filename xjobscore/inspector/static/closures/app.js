import { api } from "/static/shared/api.js";
import { el, formatCount as count } from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";

renderNav("Closed jobs");
const $ = id => document.getElementById(id);
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const REASONS = {
  ats: "ATS marked closed", dead: "Dead link · 404/410", message: "Closure message",
  redirect: "Page redirected", page_unknown: "Page closure · detail unavailable", unknown: "Evidence unavailable",
};
const OUTCOMES = { unchecked: "Unchecked", inconclusive: "Inconclusive", live: "Last check live", no_url: "No check URL", cancelled: "Check cancelled", absent: "Dead link", page_closed: "Page closed" };
const AGES = ["< 1h", "1–6h", "6–24h", "1–3d", "3d+"];
let data, selection = { view: "closed" }, offset = 0, request = 0, summaryRequest = 0;
const number = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const date = value => value ? new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "Unknown";
const dayLabel = value => new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const duration = hours => hours == null ? "—" : hours < 1 ? `${Math.round(hours * 60)}m` : hours < 48 ? `${hours.toFixed(1)}h` : `${(hours / 24).toFixed(1)}d`;
const series = reason => `--series: var(--${reason})`;
const empty = message => el("div", { class: "empty-state" }, message);
const params = () => ({ days: $("period").value, tz });
const safeLink = url => {
  try { return ["http:", "https:"].includes(new URL(url).protocol) ? url : null; } catch { return null; }
};

function select(filters, scroll = true) {
  selection = { view: "closed", ...filters };
  $("search").value = "";
  offset = 0;
  loadRows();
  if (scroll) $("postings-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function metric(label, value, note, onclick) {
  return el("button", { class: "metric", type: "button", onclick },
    el("span", { class: "metric-label" }, label),
    el("span", { class: "metric-value" }, value),
    el("span", { class: "metric-note" }, note));
}

function renderMetrics() {
  const m = data.metrics;
  $("metrics").replaceChildren(
    metric("Closures observed", count(m.closed), `${count(m.closed_24h)} detected in the last 24h`, () => select({})),
    metric("Missing, unresolved", count(m.unresolved), `${count(m.over_24h)} missing for more than 24h`, () => select({ view: "unresolved" })),
    metric("p95 confirmation time", duration(m.p95_hours), `${count(m.lag_sample)} completed closures with timing`, () => $("lag").scrollIntoView({ behavior: "smooth", block: "center" })),
    metric("Listed after closure", count(m.review), "Current listing · review candidates", () => select({ view: "review" })),
  );
  $("coverage").textContent = `Observed since ${date(data.history_since)} · ${tz} · Snapshot ${date(data.observed_at)} · Refreshes at most once a minute${m.undated ? ` · ${count(m.undated)} undated closures excluded from date charts` : ""}`;
}

function renderTrend() {
  const totals = data.daily.map(d => Object.values(d.counts).reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...totals);
  const scale = Math.max(4, Math.ceil(max / 4) * 4);
  const grid = el("div", { class: "gridlines", "aria-hidden": "true" },
    [4, 3, 2, 1].map(n => el("div", { class: "gridline" }, el("span", {}, number.format(scale * n / 4)))));
  const step = Math.ceil(data.daily.length / 7);
  const columns = data.daily.map((day, i) => el("div", { class: "column" },
    el("div", { class: "stack", style: `height:${totals[i] / scale * 178}px` },
      Object.keys(REASONS).filter(reason => day.counts[reason]).map(reason => {
        const label = `${dayLabel(day.day)}: ${count(day.counts[reason])} · ${REASONS[reason]}`;
        return el("button", { class: "segment", type: "button", style: `${series(reason)};height:${day.counts[reason] / totals[i] * 100}%`,
          title: label, "aria-label": label, onclick: () => select({ day: day.day, reason }) });
      })),
    el("span", { class: "x-label" }, i % step === 0 || i === data.daily.length - 1 ? dayLabel(day.day) : "")));
  $("trend").replaceChildren(data.daily.length && data.metrics.closed ? el("div", { class: "trend", role: "group", "aria-label": "Daily closures by evidence; select a segment to see jobs" }, grid, el("div", { class: "columns" }, columns)) : empty("No dated closures observed in this period."));
  $("legend").replaceChildren(...Object.entries(REASONS).filter(([reason]) => data.evidence[reason]).map(([reason, label]) =>
    el("button", { type: "button", onclick: () => select({ reason }) }, el("span", { class: "swatch", style: series(reason) }), label)));
}

function renderEvidence() {
  const total = data.metrics.closed;
  const rows = Object.entries(data.evidence).sort((a, b) => b[1] - a[1]);
  $("evidence").replaceChildren(...(rows.length ? rows.map(([reason, n]) => el("button", {
    class: "evidence-row", type: "button", style: series(reason), onclick: () => select({ reason }),
    "aria-label": `${REASONS[reason]}: ${count(n)} postings, ${(n / total * 100).toFixed(1)} percent; inspect postings`,
  }, el("div", { class: "bar-caption" }, el("span", { class: "label" }, el("span", { class: "swatch" }), REASONS[reason]),
    el("span", { class: "count" }, `${count(n)} · ${(n / total * 100).toFixed(1)}%`)),
  el("div", { class: "track" }, el("div", { class: "fill", style: `--width:${n / total * 100}%` })))) : [empty("No closures in this period.")]));
}

function renderBacklog() {
  $("outcomes").replaceChildren(...Object.entries(data.outcomes).sort((a, b) => b[1] - a[1]).map(([outcome, n]) =>
    el("button", { class: "outcome", type: "button", onclick: () => select({ view: "unresolved", outcome }) }, OUTCOMES[outcome] || outcome, el("strong", {}, count(n)))));
  const vendors = data.vendors.slice(0, 8);
  const max = Math.max(1, ...vendors.flatMap(v => v.ages));
  $("backlog").replaceChildren(vendors.length ? el("table", { class: "heatmap", "aria-label": "Currently unresolved missing postings by provider and age" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Provider"), AGES.map(a => el("th", { scope: "col" }, a)), el("th", { scope: "col" }, "Total"))),
    el("tbody", {}, vendors.map(v => el("tr", {},
      el("td", {}, el("button", { class: "vendor", type: "button", onclick: () => select({ view: "unresolved", vendor: v.vendor }) }, v.vendor)),
      v.ages.map((n, age) => el("td", {}, el("button", { type: "button", style: `--intensity:${n ? 10 + Math.sqrt(n / max) * 60 : 0}%`,
        title: `${v.vendor} · ${AGES[age]} missing · ${count(n)} postings`, "aria-label": `${v.vendor}, missing ${AGES[age]}: ${count(n)} postings`,
        onclick: () => select({ view: "unresolved", vendor: v.vendor, age }) }, n ? number.format(n) : "—"))),
      el("td", { class: "total" }, number.format(v.total)))))) : empty("No unresolved missing postings."));
  $("backlog-note").textContent = `${vendors.length < data.vendors.length ? `Top ${vendors.length} of ${data.vendors.length} providers. ` : ""}Click a cell to inspect its jobs. A live last check does not clear board absence.`;
}

function renderLag() {
  const max = Math.max(1, ...data.lag.map(b => b.count));
  $("lag").replaceChildren(data.metrics.lag_sample ? el("div", {},
    el("div", { class: "lag-bars", role: "group", "aria-label": "Time from missing to closure confirmation" }, data.lag.map((b, age) => el("div", { class: "lag-column" },
      el("span", { class: "lag-count" }, count(b.count)),
      el("button", { class: "lag-bar", type: "button", style: `height:${b.count / max * 145}px`,
        title: `${b.label}: ${count(b.count)} closures`, "aria-label": `${b.label}: ${count(b.count)} closures`, onclick: () => select({ age }) })))),
    el("div", { class: "lag-labels", "aria-hidden": "true" }, data.lag.map(b => el("span", {}, b.label)))) : empty("No closures with usable missing-to-confirmation timing."));
  $("lag-note").textContent = `Median ${duration(data.metrics.median_hours)} · ${count(data.metrics.lag_sample)} samples. Completed cases only; unresolved jobs are shown separately. Missing timestamps may reset when a job returns.`;
}

function renderSelection() {
  $("tabs").replaceChildren(...[["closed", "Closed"], ["unresolved", "Unresolved"], ["review", "Review"]].map(([view, label]) =>
    el("button", { type: "button", "aria-pressed": String(selection.view === view), onclick: () => select({ view }, false) }, label)));
  const filters = [selection.vendor, selection.reason && REASONS[selection.reason], selection.day && dayLabel(selection.day),
    selection.age != null && `${AGES[selection.age]} ${selection.view === "unresolved" ? "missing" : "to confirm"}`,
    selection.outcome && OUTCOMES[selection.outcome]].filter(Boolean);
  $("selection").replaceChildren(...(filters.length ? [el("span", {}, filters.join(" · ")), el("button", { type: "button", onclick: () => select({ view: selection.view }, false) }, "Clear filters")] : []));
  $("date-heading").textContent = selection.view === "unresolved" ? "Missing since" : "Closure detected";
  $("table-description").textContent = {
    closed: "Detected closures in the selected period. Newest first.",
    unresolved: "Currently missing and still active. Oldest first; all ages.",
    review: "Closed by a link or page check, then listed again. Review the evidence before drawing a conclusion.",
  }[selection.view];
}

function evidence(row) {
  const unresolved = selection.view === "unresolved";
  const label = unresolved ? OUTCOMES[row.outcome] || row.outcome : REASONS[row.reason];
  const node = el("td", {}, el("span", { class: "reason" }, unresolved ? null : el("span", { class: "swatch", style: series(row.reason) }), label));
  const items = [];
  if (unresolved) {
    items.push(`Latest check: ${date(row.last_checked_at)}`);
    if (row.last_error) items.push(row.last_error);
  } else {
    if (row.detail) items.push(row.detail);
    if (row.http_status) items.push(`HTTP ${row.http_status}`);
    if (row.checked_url) items.push(`Checked: ${row.checked_url}`);
    if (row.final_url) items.push(`Landed: ${row.final_url}`);
    for (const [key, label] of [["ats_closed_at", "ATS closed"], ["linked_dead_at", "Dead link"], ["paged_closed_at", "Page closure"]]) {
      if (row[key]) items.push(`${label}: ${date(row[key])}`);
    }
    if (row.missing_since) items.push(`Missing since: ${date(row.missing_since)}`);
    if (row.lag_hours != null) items.push(`Time to confirm: ${duration(row.lag_hours)}`);
  }
  items.push(`Last listed: ${date(row.last_seen_at)}`);
  node.append(el("details", {}, el("summary", {}, "Observations"), items.map(item => el("p", {}, item))));
  return node;
}

async function loadRows() {
  const current = ++request;
  renderSelection();
  $("table-status").textContent = "Loading postings…";
  $("rows").replaceChildren();
  $("page-count").textContent = "";
  $("previous").disabled = true;
  $("next").disabled = true;
  const query = new URLSearchParams({ ...params(), ...selection, q: $("search").value, offset, limit: 25 });
  try {
    const payload = await api(`/api/closures/postings?${query}`);
    if (current !== request) return;
    $("table-status").textContent = payload.rows.length ? "" : "No postings match this selection.";
    $("rows").replaceChildren(...payload.rows.map(row => {
      const href = safeLink(row.source_url) || safeLink(row.apply_url);
      const when = selection.view === "unresolved" ? row.missing_since : row.detected_at;
      return el("tr", {},
        el("td", {}, el("strong", {}, row.company), el("span", { class: "job-title" }, row.title), row.location_text && el("span", { class: "sub" }, row.location_text)),
        el("td", {}, row.source.split(":")[0], el("span", { class: "sub" }, row.source)),
        el("td", {}, date(when), row.posted_at && el("span", { class: "sub" }, `Source posted ${date(row.posted_at)}`)),
        evidence(row), el("td", {}, href && el("a", { href, target: "_blank", rel: "noopener noreferrer" }, "Source ↗")));
    }));
    $("page-count").textContent = payload.total ? `${count(offset + 1)}–${count(offset + payload.rows.length)} of ${count(payload.total)} postings` : "0 postings";
    $("previous").disabled = offset === 0;
    $("next").disabled = offset + 25 >= payload.total;
  } catch (error) {
    if (current === request) $("table-status").textContent = `Postings unavailable: ${error.message}`;
  }
}

async function load() {
  const current = ++summaryRequest;
  $("refresh").disabled = true;
  $("error").hidden = true;
  $("loading").hidden = false;
  $("dashboard").hidden = true;
  try {
    const payload = await api(`/api/closures?${new URLSearchParams(params())}`);
    if (current !== summaryRequest) return;
    data = payload;
    renderMetrics(); renderTrend(); renderEvidence(); renderBacklog(); renderLag();
    $("dashboard").hidden = false;
    await loadRows();
  } catch (error) {
    if (current === summaryRequest) {
      $("error").textContent = `Closure analytics unavailable: ${error.message}`;
      $("error").hidden = false;
    }
  } finally {
    if (current === summaryRequest) { $("loading").hidden = true; $("refresh").disabled = false; }
  }
}

$("refresh").addEventListener("click", load);
$("period").addEventListener("change", () => { selection = { view: selection.view }; offset = 0; load(); });
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - 25); loadRows(); });
$("next").addEventListener("click", () => { offset += 25; loadRows(); });
let searchTimer;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { offset = 0; loadRows(); }, 250); });
load();
