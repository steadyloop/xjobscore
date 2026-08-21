/* Scheduler: what production polling actually did, per feed.
 *
 * Every number here comes from public.poll_observation, which run.py appends to
 * once per completed poll. A fresh database still has none of those rows, so the
 * page keeps three legitimate states -- DB down, table empty, table populated --
 * and unknown values render as "—", never as 0. A zero would claim the poller
 * ran and found nothing.
 *
 * The table is read-only by design: it is for comparing feeds. Everything that
 * changes a feed lives in the detail pane, where there is room to show what the
 * change would cost.
 */

import { api, isDegraded } from "/static/shared/api.js";
import {
  el,
  formatCount,
  formatInterval,
  relativeTime,
  statusChip,
} from "/static/shared/format.js";
import { renderNav } from "/static/shared/nav.js";
import { sparkBars } from "/static/shared/spark.js";

renderNav("Scheduler");

const RENDER_CHUNK = 200;
const SEARCH_DEBOUNCE_MS = 180;
const OVERVIEW_POLL_MS = 30_000;
const ARM_TIMEOUT_MS = 4000;
// How long Poll now waits before re-reading the feed. The runner wakes at once,
// so this only has to cover one HTTP fetch of a job board plus the write.
const POLL_NOW_SETTLE_MS = 3000;
const DETAIL_WIDTH_KEY = "inspector-scheduler-detail-width";
const VENDOR_OPEN_KEY = "inspector-scheduler-vendors-open";
const LATENCY_TARGET_S = 1800;
// Worker count for Fetch all. Same reasoning as the feeds page: this only
// bounds in-flight browser requests -- the server-side shared Net (per-host
// lock + adaptive pacing) is what actually protects a single-host vendor
// like Ashby from 8 concurrent polls.
const BULK_FETCH_CONCURRENCY = 8;

const TIERS = [
  [300, "5m"],
  [900, "15m"],
  [3600, "1h"],
  [21600, "6h"],
  [86400, "24h"],
];

// Sort order for the table: problems first, deliberate states last. "paused"
// sits down with "disabled" because a held feed is not a thing to fix.
const STATE_ORDER = [
  "backing_off",
  "stale",
  "never_polled",
  "calibrating",
  "healthy",
  "paused",
  "disabled",
  "unknown",
];

const state = {
  feeds: [],
  overview: null,
  telemetryAvailable: false,
  connected: false,
  control: null, // {running, changed_at, note} -- the operator's pause/resume switch
  runner: null, // {alive, last_cycle_at, last_polled, consecutive_errors, last_error}
  selectedRef: null,
  search: "",
  platform: "",
  interval: "",
  status: "",
  bulkFetch: null, // {completed, total, failed, cancelled} while Fetch all runs
};

const masterEl = document.getElementById("master");
const paneEl = document.getElementById("detail-pane");
const handleEl = document.getElementById("split-handle");
const emptyEl = document.getElementById("sched-empty");
const countEl = document.getElementById("row-count");
// Reassigned on every render: chunked rendering swaps in a fresh tbody so a
// superseded pass can detect it was replaced and stop.
let bodyEl = document.getElementById("sched-body");

const storedWidth = Number(localStorage.getItem(DETAIL_WIDTH_KEY));
if (storedWidth) masterEl.style.setProperty("--detail-width", `${storedWidth}px`);

/* -------------------------------------------------------------- formatting */

function tierLabel(seconds) {
  if (seconds == null) return "—";
  const preset = TIERS.find(([value]) => value === seconds);
  return preset ? preset[1] : formatInterval(seconds);
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172_800) return `${(seconds / 3600).toFixed(1)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

function formatLambda(value) {
  return value == null ? "—" : value.toFixed(1);
}

/* ---------------------------------------------------------------- loading */

async function loadOverview() {
  const view = await api("/api/scheduler/overview");
  state.overview = view;
  state.connected = Boolean(view.postgres?.connected);
  state.telemetryAvailable = Boolean(view.telemetry_available);
  state.control = view.control ?? null;
  state.runner = view.runner ?? null;
  renderRunToggle();
  renderStats();
  renderBanners();
  renderVendors();
}

async function loadFeeds() {
  const view = await api("/api/scheduler/feeds");
  state.feeds = view.feeds ?? [];
  state.connected = Boolean(view.postgres?.connected);
  state.telemetryAvailable = Boolean(view.telemetry_available);
  populateFilters();
  renderBanners();
  renderVendors();
  renderTable();
}

/* --------------------------------------------------------------- banners */

function renderBanners() {
  document.getElementById("degraded-banner").hidden = state.connected;
  // Only a second complaint when PostgreSQL is up: with the DB down the
  // degraded banner has already said everything this one would.
  document.getElementById("telemetry-banner").hidden =
    !state.connected || state.telemetryAvailable;
  // Mid-run the button doubles as Stop; the 30s overview poll must not
  // re-enable/disable it out from under a click.
  if (!state.bulkFetch) {
    document.getElementById("fetch-all").disabled = !state.connected;
  }

  const running = state.control?.running ?? false;
  document.getElementById("paused-banner").hidden = !state.connected || running;
  // The flag says "run" but nothing is listening -- a distinct problem from
  // "paused", and one the pause banner would misreport as fine.
  document.getElementById("runner-missing-banner").hidden =
    !state.connected || !running || Boolean(state.runner?.alive);
}

function renderRunToggle() {
  const button = document.getElementById("run-toggle");
  const running = state.control?.running ?? false;
  button.disabled = !state.connected;
  button.textContent = running ? "Pause scheduler" : "Resume scheduler";
  button.classList.toggle("primary", !running);
  button.title = running
    ? "Stop the production polling loop"
    : "Start the production polling loop";
}

/* ----------------------------------------------------------------- tiles */

function statBlock(label, value, { sub = null, delta = null } = {}) {
  return el(
    "div",
    { class: "stat" },
    el("div", { class: "label" }, label),
    el(
      "div",
      { class: "sched-stat-row" },
      el("div", { class: "value" }, value),
      delta
    ),
    sub ? el("div", { class: "secondary" }, sub) : null
  );
}

function latencyDelta(tiles) {
  const now = tiles?.discovery_latency_p95_s;
  const before = tiles?.discovery_latency_p95_prev_s;
  if (now == null || before == null) return null;
  const change = now - before;
  if (Math.abs(change) < 60) return el("span", { class: "secondary" }, "flat");
  const improved = change < 0;
  return el(
    "span",
    {
      class: improved ? "sched-delta-good" : "secondary",
      title: `previous period: ${formatDuration(before)}`,
    },
    `${improved ? "−" : "+"}${formatDuration(Math.abs(change))} vs prior period`
  );
}

function schedulerTile(control, runner) {
  const running = control?.running ?? false;
  const alive = Boolean(runner?.alive);
  const value = !running ? "Paused" : alive ? "Running" : "Run set, idle";
  const parts = [];
  if (running && !alive) parts.push("no process is supervising it");
  if (runner?.last_cycle_at) parts.push(`last cycle ${relativeTime(runner.last_cycle_at)}`);
  if (runner?.last_polled != null) parts.push(`${formatCount(runner.last_polled)} feeds last cycle`);
  if (runner?.consecutive_errors) parts.push(`${runner.consecutive_errors} failed ticks in a row`);
  return statBlock("Scheduler", value, {
    sub: parts.length ? parts.join(" · ") : running ? "no cycles yet" : "",
  });
}

function renderStats() {
  const view = state.overview;
  if (!view) return;
  const tiles = view.tiles;
  const counts = view.counts_by_status ?? {};
  // "paused" is deliberate, so it is not attention -- it is reported separately.
  const attention = ["backing_off", "stale"]
    .map((name) => [name, counts[name] ?? 0])
    .filter(([, n]) => n > 0)
    .map(([name, n]) => `${formatCount(n)} ${name.replace("_", " ")}`);

  const perPosting =
    tiles?.new_postings_today > 0
      ? `${(tiles.requests_today / tiles.new_postings_today).toFixed(1)} per posting discovered`
      : "no postings discovered today";

  const worst = tiles?.error_sources?.[0];

  document.getElementById("sched-stats").replaceChildren(
    schedulerTile(view.control, view.runner),
    statBlock("Feeds scheduled", formatCount(view.feed_count), {
      sub: attention.length ? attention.join(" · ") : "none need attention",
    }),
    statBlock(
      "Discovery latency p95",
      formatDuration(tiles?.discovery_latency_p95_s),
      {
        delta: latencyDelta(tiles),
        sub: `posted → first seen, ${tiles?.latency_window_days ?? 7} d · target ≤ ${formatDuration(LATENCY_TARGET_S)}`,
      }
    ),
    statBlock("Polls today", formatCount(tiles?.requests_today), {
      delta: tiles?.polls_by_hour?.length
        ? sparkBars(
            tiles.polls_by_hour.map((value, index) => ({
              value,
              label: `${tiles.polls_by_hour.length - index} h ago · ${value} polls`,
            })),
            { width: 96, height: 26, fluid: false }
          )
        : null,
      sub: perPosting,
    }),
    statBlock("Errors, 24 h", formatCount(tiles?.errors_24h), {
      sub: worst
        ? `${formatCount(worst.errors)} of ${formatCount(tiles.errors_24h)} from ${worst.source}`
        : "no failed polls",
    })
  );
}

/* --------------------------------------------------------- vendor rollup */

/* What production is asking each vendor to absorb.
 *
 * Rate limits are a vendor-level resource -- net.py caps concurrency per
 * hostname suffix, and a job board's tolerance is a property of the vendor, not
 * of any one tenant feed -- but every number on the table below is per feed. A
 * thousand Ashby tenants at a 5m tier is a decision nobody makes explicitly and
 * that no row shows; this is where it becomes visible.
 *
 * Computed over every scheduled feed, deliberately NOT over the filtered set:
 * the question is what the vendor actually receives, and that total does not
 * shrink because the table below is narrowed to stale rows. Clicking a row is
 * what narrows the table -- the roll-up stays whole.
 *
 * Paused feeds are excluded from both cost columns and counted separately. A
 * held feed issues no requests, so folding its configured interval into the
 * total would bill the vendor for polls that are not happening.
 */
function vendorRollup() {
  const errorsBySource = new Map(
    (state.overview?.tiles?.error_sources ?? []).map((row) => [row.source, row.errors])
  );

  const vendors = new Map();
  for (const feed of state.feeds) {
    let agg = vendors.get(feed.source);
    if (!agg) {
      agg = {
        source: feed.source,
        feeds: 0,
        paused: 0,
        current: 0,
        recommended: 0,
        unpriced: 0, // active feeds with no interval — cost unknown, not zero
        unrated: 0, // active feeds λ has no opinion on yet
        attention: new Map(),
      };
      vendors.set(feed.source, agg);
    }
    agg.feeds += 1;

    const status = feed.sched.status.state;
    if (status === "backing_off" || status === "stale") {
      agg.attention.set(status, (agg.attention.get(status) ?? 0) + 1);
    }
    if (feed.policy?.paused_at) {
      agg.paused += 1;
      continue;
    }

    const current = feed.sched.requests_per_day;
    if (current == null) {
      agg.unpriced += 1;
      continue;
    }
    agg.current += current;
    // A feed λ cannot rank yet carries its current cost into the recommended
    // column, so the two totals compare like for like: the delta then reads as
    // "what retiering would change", not "what we failed to estimate".
    const ideal = feed.sched.ideal_interval_s;
    if (ideal == null) {
      agg.unrated += 1;
      agg.recommended += current;
    } else {
      agg.recommended += 86_400 / ideal;
    }
  }

  for (const agg of vendors.values()) {
    agg.errors = errorsBySource.get(agg.source) ?? 0;
  }
  // Biggest consumer first: the vendor most worth looking at is the one being
  // asked for the most, not the one earliest in the alphabet.
  return [...vendors.values()].sort(
    (a, b) => b.current - a.current || a.source.localeCompare(b.source)
  );
}

/* Signed change against the current cost, shown only when it is worth acting
   on. Under 5% is retiering noise and would read as a to-do that isn't one. */
function budgetDelta(agg) {
  if (!agg.current) return null;
  const change = agg.recommended - agg.current;
  const ratio = change / agg.current;
  if (Math.abs(ratio) < 0.05) return el("span", { class: "secondary" }, "flat");
  const cheaper = change < 0;
  return el(
    "span",
    {
      class: cheaper ? "sched-delta-good" : "sched-delta-cost",
      title: cheaper
        ? `${formatCount(Math.round(-change))} fewer requests/day at the recommended tiers`
        : `${formatCount(Math.round(change))} more requests/day at the recommended tiers`,
    },
    `${cheaper ? "−" : "+"}${Math.abs(Math.round(ratio * 100))}%`
  );
}

/* The caveats that keep a total honest, as one line under the vendor name. */
function vendorNote(agg) {
  const parts = [];
  if (agg.paused) parts.push(`${formatCount(agg.paused)} paused, not counted`);
  if (agg.unpriced) parts.push(`${formatCount(agg.unpriced)} without a tier`);
  if (agg.unrated) parts.push(`${formatCount(agg.unrated)} λ can't rank yet`);
  return parts.length ? el("div", { class: "secondary" }, parts.join(" · ")) : null;
}

function vendorRow(agg, totalErrors) {
  const selected = state.platform === agg.source;
  const attention = [...agg.attention].map(([name, count]) =>
    el(
      "span",
      { class: "badge", "data-kind": name === "backing_off" ? "error" : "would_park" },
      `${formatCount(count)} ${name.replace("_", " ")}`
    )
  );

  return el(
    "tr",
    {
      class: "row-link",
      tabindex: "0",
      "aria-selected": selected ? "true" : "false",
      title: selected
        ? "Showing only this vendor — click to clear the filter"
        : `Filter the table below to ${agg.source}`,
      onclick: () => filterToVendor(selected ? "" : agg.source),
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          filterToVendor(selected ? "" : agg.source);
        }
      },
    },
    el(
      "td",
      {},
      el("span", { class: "feed-name" }, agg.source),
      vendorNote(agg)
    ),
    el("td", { class: "num" }, formatCount(agg.feeds)),
    el("td", { class: "num" }, formatCount(Math.round(agg.current))),
    el(
      "td",
      { class: "num" },
      el(
        "span",
        { class: "sched-budget-cell" },
        formatCount(Math.round(agg.recommended)),
        budgetDelta(agg)
      )
    ),
    el("td", {}, attention.length ? attention : el("span", { class: "muted" }, "—")),
    el(
      "td",
      { class: "num" },
      agg.errors
        ? el(
            "span",
            {
              title: totalErrors
                ? `${formatCount(agg.errors)} of ${formatCount(totalErrors)} failed polls in the last 24 h`
                : null,
            },
            formatCount(agg.errors)
          )
        : el("span", { class: "muted" }, "—")
    )
  );
}

function totalsRow(vendors) {
  const sum = (key) => vendors.reduce((total, agg) => total + agg[key], 0);
  return el(
    "tr",
    { class: "group-row sched-vendor-total" },
    el("td", {}, `${vendors.length} vendors`),
    el("td", { class: "num" }, formatCount(sum("feeds"))),
    el("td", { class: "num" }, formatCount(Math.round(sum("current")))),
    el("td", { class: "num" }, formatCount(Math.round(sum("recommended")))),
    el("td", {}),
    el("td", { class: "num" }, formatCount(sum("errors")))
  );
}

function renderVendors() {
  const card = document.getElementById("vendor-rollup");
  // With PostgreSQL down there are no intervals, so every cost here would be a
  // column of dashes; the degraded banner already says why. Nothing scheduled
  // is the empty-state the table below owns.
  if (!state.connected || !state.feeds.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  const vendors = vendorRollup();
  const totalErrors = state.overview?.tiles?.errors_24h ?? 0;
  document.getElementById("vendor-scope").textContent =
    `${formatCount(state.feeds.length)} scheduled feeds · totals ignore the filters below`;

  document.getElementById("vendor-body").replaceChildren(
    ...vendors.map((agg) => vendorRow(agg, totalErrors)),
    // One vendor is its own total; a second row of identical numbers is noise.
    vendors.length > 1 ? totalsRow(vendors) : null
  );
}

function filterToVendor(source) {
  state.platform = source;
  document.getElementById("filter-platform").value = source;
  renderVendors();
  renderTable();
}

function wireVendorToggle(button) {
  const wrap = document.getElementById("vendor-wrap");
  const apply = (open) => {
    wrap.hidden = !open;
    button.textContent = open ? "Hide" : "Show";
    button.setAttribute("aria-expanded", String(open));
    localStorage.setItem(VENDOR_OPEN_KEY, open ? "1" : "0");
  };
  apply(localStorage.getItem(VENDOR_OPEN_KEY) !== "0");
  button.addEventListener("click", () =>
    apply(button.getAttribute("aria-expanded") !== "true")
  );
}

/* ---------------------------------------------------------------- filters */

function populateFilters() {
  fillSelect(
    "filter-platform",
    [...new Set(state.feeds.map((feed) => feed.source))].sort(),
    (value) => value,
    state.platform
  );
  const intervals = [
    ...new Set(
      state.feeds.map((feed) => feed.policy?.interval_s).filter((value) => value)
    ),
  ].sort((a, b) => a - b);
  fillSelect("filter-interval", intervals, tierLabel, state.interval);

  // The server owns the state→label mapping; read it back off the rows rather
  // than keeping a second copy here that could drift.
  const labels = new Map(
    state.feeds.map((feed) => [feed.sched.status.state, feed.sched.status.label])
  );
  const states = STATE_ORDER.filter((name) => labels.has(name));
  fillSelect("filter-status", states, (value) => labels.get(value), state.status);
}

function fillSelect(id, values, label, selected) {
  const select = document.getElementById(id);
  select.replaceChildren(
    select.options[0], // the "All …" option, reused verbatim
    ...values.map((value) => el("option", { value: String(value) }, label(value)))
  );
  select.value = String(selected ?? "");
}

function matches(feed) {
  if (state.platform && feed.source !== state.platform) return false;
  if (state.interval && String(feed.policy?.interval_s) !== state.interval) return false;
  if (state.status && feed.sched.status.state !== state.status) return false;
  if (!state.search) return true;
  const needle = state.search.toLowerCase();
  return (
    feed.source.toLowerCase().includes(needle) ||
    feed.key.toLowerCase().includes(needle) ||
    (feed.company_name ?? "").toLowerCase().includes(needle)
  );
}

/* ------------------------------------------------------------------ cells */

function dash(className) {
  return el("td", { class: className ?? null }, el("span", { class: "muted" }, "—"));
}

function tierCell(feed) {
  if (!state.connected || !feed.policy?.in_postgres) return dash();
  const verdict = feed.sched.verdict;
  const ideal = feed.sched.ideal_interval_s;
  return el(
    "td",
    {},
    el("span", { class: "sched-tier" }, tierLabel(feed.policy.interval_s)),
    verdict && verdict !== "ok"
      ? el(
          "span",
          {
            class: "badge sched-rec",
            "data-kind": verdict === "too_short" ? "would_park" : "fallback",
            title: `λ suggests ${tierLabel(ideal)}`,
          },
          `→ ${tierLabel(ideal)}`
        )
      : null
  );
}

function lambdaCell(feed) {
  const lam = feed.sched.lambda;
  if (lam == null) return dash("num");
  return el("td", { class: "num" }, formatLambda(lam));
}

function statusCell(feed) {
  const status = feed.sched.status;
  const chip = statusChip(status.chip, status.label);
  chip.title = status.detail;
  return el("td", {}, chip);
}

function feedRow(feed) {
  return el(
    "tr",
    {
      class: "row-link",
      tabindex: "0",
      "aria-selected": feed.feed_ref === state.selectedRef ? "true" : "false",
      onclick: () => selectFeed(feed.feed_ref),
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectFeed(feed.feed_ref);
        }
      },
    },
    // Name only. The source:key slug lives in the detail pane, where there is
    // room for it; repeating it on every row costs width the numbers need.
    el("td", { class: "feed-cell" }, el("span", { class: "feed-name" }, feed.company_name || feed.key)),
    el("td", { class: "secondary" }, feed.source),
    tierCell(feed),
    lambdaCell(feed),
    feed.telemetry ? el("td", { class: "num" }, formatCount(feed.telemetry.new_7d)) : dash("num"),
    state.connected
      ? el("td", { class: "num secondary" }, relativeTime(feed.policy?.last_poll_at))
      : dash("num"),
    el("td", { class: "num" }, formatDuration(feed.postings?.p95_latency_s)),
    statusCell(feed)
  );
}

/* ------------------------------------------------------------------ table */

function renderTable() {
  const visible = state.feeds.filter(matches);
  countEl.textContent =
    `${formatCount(visible.length)} of ${formatCount(state.feeds.length)} feeds · sorted by λ`;
  emptyEl.hidden = visible.length > 0;
  // Nothing enabled is a different problem from a filter that excludes
  // everything, and it has a different fix -- say which one this is.
  emptyEl.textContent = state.feeds.length
    ? "No feeds match the current filters."
    : "No feeds are enabled. Switch one on from the Feeds page to schedule it.";

  const cursor = document.createElement("tbody");
  cursor.id = "sched-body";
  bodyEl.replaceWith(cursor);
  bodyEl = cursor;

  let index = 0;
  function step() {
    if (!cursor.isConnected) return; // a re-render replaced this table body
    for (const feed of visible.slice(index, index + RENDER_CHUNK)) {
      cursor.append(feedRow(feed));
    }
    index += RENDER_CHUNK;
    if (index < visible.length) requestAnimationFrame(step);
  }
  step();
}

/* ------------------------------------------------------------ detail pane */

function dayLabels(count) {
  const labels = [];
  for (let offset = count - 1; offset >= 0; offset -= 1) {
    const date = new Date();
    date.setUTCDate(date.getUTCDate() - offset);
    labels.push(date.toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  }
  return labels;
}

function kv(term, definition) {
  return [el("dt", {}, term), el("dd", {}, definition)];
}

function tierControl(feed) {
  const disabled = !state.connected || !feed.policy?.in_postgres;
  const current = feed.policy?.interval_s;

  // "Auto" is present because adaptive tiering is the intended end state, and
  // disabled because nothing implements it: there is no interval_auto column
  // and nothing writes interval_s from λ. Showing it enabled would promise a
  // behavior that does not exist.
  const auto = el(
    "button",
    {
      type: "button",
      disabled: "",
      "aria-pressed": "false",
      title: "Adaptive tiering isn't implemented yet — λ only recommends",
    },
    "Auto"
  );

  return el(
    "div",
    { class: "sched-seg", role: "group", "aria-label": "Polling tier" },
    auto,
    TIERS.map(([seconds, label]) =>
      el(
        "button",
        {
          type: "button",
          "aria-pressed": seconds === current ? "true" : "false",
          disabled: disabled ? "" : null,
          onclick: () => setInterval_(feed, seconds),
        },
        label
      )
    )
  );
}

function renderDetail(feed, events) {
  const sched = feed.sched;
  const status = sched.status;
  const spark = feed.spark ?? [];
  const labels = dayLabels(spark.length);
  const perPosting =
    sched.requests_per_day != null && sched.lambda
      ? ` · ${Math.round(sched.requests_per_day / sched.lambda)} per posting`
      : "";
  const pollBlocked = pollNowBlocked(feed);

  paneEl.replaceChildren(
    el(
      "header",
      {},
      el(
        "div",
        {},
        el("h2", {}, feed.company_name || feed.key),
        el("div", { class: "secondary" }, feed.source, " · ",
           el("span", { class: "mono" }, feed.key))
      ),
      el("div", { class: "grow" }),
      el("button", { class: "btn small", type: "button", onclick: closeDetail }, "Close")
    ),
    el(
      "div",
      { class: "body" },
      el(
        "dl",
        { class: "sched-kv" },
        kv("Status", el("span", { title: status.detail }, statusChip(status.chip, status.label))),
        kv(
          "λ estimate",
          sched.lambda == null
            ? el("span", { class: "muted" }, `calibrating (prior ${sched.prior.toFixed(1)}/day)`)
            : `${sched.lambda.toFixed(1)} postings/day`
        ),
        kv(
          "Recommended tier",
          sched.ideal_interval_s == null
            ? el("span", { class: "muted" }, "not enough history")
            : `${tierLabel(sched.ideal_interval_s)}${
                sched.verdict === "ok" ? " · matches current" : " · differs from current"
              }`
        ),
        kv(
          "Request cost",
          sched.requests_per_day == null
            ? el("span", { class: "muted" }, "—")
            : `${Math.round(sched.requests_per_day)}/day at ${tierLabel(feed.policy?.interval_s)}${perPosting}`
        ),
        kv("Open postings", formatCount(feed.postings?.open_postings)),
        kv("p95 latency", formatDuration(feed.postings?.p95_latency_s)),
        kv("Observed days", formatCount(feed.telemetry?.observed_days)),
        kv("Conditional GET", feed.policy?.has_etag ? "ETag stored" : "no validator yet"),
        kv("Strikes", formatCount(feed.policy?.strikes))
      ),

      el("h3", {}, "Postings found per day · 14 d"),
      spark.length
        ? el(
            "div",
            {},
            sparkBars(
              spark.map((value, index) => ({
                value,
                label: `${labels[index]} · ${value} posting${value === 1 ? "" : "s"}`,
              }))
            ),
            el(
              "div",
              { class: "sched-chart-cap" },
              el("span", {}, labels[0] ?? ""),
              el("span", {}, labels[labels.length - 1] ?? "")
            )
          )
        : el("div", { class: "muted" }, "No poll observations recorded yet."),

      el("h3", {}, "Polling tier"),
      tierControl(feed),
      el(
        "p",
        { class: "secondary sched-note" },
        sched.ideal_interval_s == null
          ? "λ has no recommendation yet. The tier stays exactly where you set it."
          : sched.verdict === "ok"
            ? `λ agrees with ${tierLabel(feed.policy?.interval_s)}.`
            : `λ suggests ${tierLabel(sched.ideal_interval_s)}. Advisory only — nothing changes the tier but you.`
      ),

      el("h3", {}, "Activation"),
      el(
        "div",
        { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" },
        el(
          "button",
          {
            class: "btn small",
            type: "button",
            disabled: pollBlocked ? "" : null,
            title:
              pollBlocked ?? "Poll this feed now, ahead of everything else in the queue",
            onclick: (event) => pollNow(feed, event.currentTarget),
          },
          "Poll now"
        ),
        el(
          "button",
          {
            class: feed.policy?.paused_at ? "btn small primary" : "btn small",
            type: "button",
            disabled: state.connected && feed.policy?.in_postgres ? null : "",
            onclick: () => setPaused(feed, !feed.policy?.paused_at),
          },
          feed.policy?.paused_at ? "Resume polling" : "Pause polling"
        )
      ),
      // Pause, not disable. feed.enabled is what decides whether a feed appears
      // on this page at all, so it stays owned by the Feeds page toggle --
      // switching it off from here would delete the row you are reading. Pausing
      // holds polling while leaving the feed exactly where it is.
      el(
        "p",
        { class: "secondary sched-note" },
        feed.policy?.paused_at
          ? `Held since ${relativeTime(feed.policy.paused_at)}. Production is not claiming this feed, and Poll now stays refused until you resume — which makes it due immediately.`
          : "Poll now puts this feed ahead of every other feed waiting in the queue and wakes the runner for it, so the fetch happens in seconds rather than on the next tick; vendor pacing still applies. Pausing stops production polling and keeps everything else: the feed stays enabled, stays listed here, and keeps its postings."
      ),

      el("h3", {}, "Recent polls"),
      events && events.length
        ? el(
            "ul",
            { class: "sched-events" },
            events.map((event) =>
              el(
                "li",
                {},
                el("time", {}, relativeTime(event.polled_at)),
                statusChip(event.status),
                el(
                  "span",
                  { class: "secondary" },
                  event.new_postings
                    ? `${formatCount(event.new_postings)} new`
                    : "no new postings"
                )
              )
            )
          )
        : el("div", { class: "muted" }, "No poll observations recorded yet.")
    )
  );
}

/* ------------------------------------------------------------------ writes */

async function toggleRun(button) {
  const running = state.control?.running ?? false;
  button.disabled = true;
  try {
    const view = await api("/api/scheduler/control", { body: { running: !running } });
    state.control = view.control;
    state.runner = view.runner;
    renderRunToggle();
    renderBanners();
  } catch (error) {
    alert(`Could not ${running ? "pause" : "resume"} the scheduler: ${error.message}`);
  } finally {
    button.disabled = !state.connected;
  }
}

async function setInterval_(feed, seconds) {
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { interval_s: seconds } });
    await loadFeeds();
    await selectFeed(feed.feed_ref);
  } catch (error) {
    alert(`Could not set tier: ${error.message}`);
  }
}

async function setPaused(feed, paused) {
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { paused } });
    await loadFeeds();
    await selectFeed(feed.feed_ref);
  } catch (error) {
    alert(`Could not ${paused ? "pause" : "resume"} polling: ${error.message}`);
  }
}

/* Why Poll now cannot run, as a sentence, or null when it can.
   Mirrors the refusals in policy.poll_now() and the route's runner check, so a
   blocked button says which switch to flip instead of looking available and
   failing in an alert. state.control and state.runner come from loadOverview(),
   which boot awaits before the first render and an interval keeps fresh. */
function pollNowBlocked(feed) {
  if (!state.connected) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres) return "Import this feed before polling it.";
  if (feed.policy?.enabled === false) {
    return "Feed is disabled — enable it on the Feeds page first.";
  }
  if (feed.policy?.paused_at) return "Paused — resume polling before polling it now.";
  if (feed.policy?.hold_until && new Date(feed.policy.hold_until) > new Date()) {
    return "A poll of this feed is already running.";
  }
  if (!state.control?.running) return "Scheduler is paused — resume it first.";
  if (!state.runner?.alive) return "No runner is listening, so nothing would poll it.";
  return null;
}

async function pollNow(feed, button) {
  button.disabled = true;
  const original = button.textContent;
  try {
    await api(`/api/scheduler/feeds/${feed.feed_ref}/poll-now`, { body: {} });
    button.textContent = "Polling…";
    // loadFeeds() redraws the table but not this pane, so the label survives.
    await loadFeeds();
    // The runner is woken synchronously but the fetch itself takes a moment;
    // re-select once it has plausibly landed so "Recent polls" shows the new
    // poll_observation row without a manual reload. selectFeed() rebuilds the
    // pane, which restores the button by itself.
    setTimeout(() => {
      if (state.selectedRef === feed.feed_ref) {
        selectFeed(feed.feed_ref).catch(() => {});
      }
      button.textContent = original;
      button.disabled = false;
    }, POLL_NOW_SETTLE_MS);
  } catch (error) {
    button.textContent = original;
    button.disabled = false;
    alert(`Could not poll this feed: ${error.message}`);
  }
}

/* Feeds this run would actually poll: whatever the filters show, minus paused
   rows -- those stay visible in the table but a fetch would only 409, and a
   held feed is not a thing this button should un-hold. */
function fetchCandidates() {
  return state.feeds.filter(matches).filter((feed) => !feed.policy?.paused_at);
}

/* Two-click arm, then a browser-driven worker pool over the visible feeds --
   the same mechanics as the feeds page's wireFetchAllButton (feeds.js), but
   against /api/scheduler/feeds/{ref}/fetch, so every poll runs the production
   path (raw_posting, job_posting, poll_observation) instead of staging.
   While running, the button doubles as Stop. Deliberately no renderTable()
   mid-run: it swaps the whole tbody, which would yank the table around under
   the operator; one loadFeeds() at the end shows the real poll results. */
function wireFetchAllButton(button) {
  const original = button.textContent;
  const originalTitle = button.title;
  let armed = false;
  let timer = null;
  let progress = null;

  function disarm() {
    armed = false;
    button.textContent = original;
    button.classList.remove("primary");
    clearTimeout(timer);
  }

  button.addEventListener("click", async () => {
    if (progress) {
      progress.cancelled = true;
      button.textContent = `Stopping… ${progress.completed}/${progress.total}`;
      return;
    }
    if (!armed) {
      const count = fetchCandidates().length;
      if (!count) return;
      armed = true;
      button.textContent = `Confirm — fetch ${formatCount(count)} feeds live`;
      button.classList.add("primary");
      timer = setTimeout(disarm, ARM_TIMEOUT_MS);
      return;
    }
    disarm();

    const feeds = fetchCandidates();
    progress = { completed: 0, total: feeds.length, failed: 0, cancelled: false };
    state.bulkFetch = progress;
    button.classList.add("primary");
    button.title = "Click to stop";
    button.textContent = `Fetching 0/${progress.total}…`;

    let cursor = 0;
    async function worker() {
      while (cursor < feeds.length && !progress.cancelled) {
        const feed = feeds[cursor++];
        try {
          const result = await api(`/api/scheduler/feeds/${feed.feed_ref}/fetch`, {
            body: {},
          });
          // A refused claim throws (409); a harvest that ran and failed is a
          // 200 whose status says so. Both are failures to the counter.
          if (result?.status === "error") progress.failed += 1;
        } catch {
          progress.failed += 1;
        }
        progress.completed += 1;
        button.textContent = progress.cancelled
          ? `Stopping… ${progress.completed}/${progress.total}`
          : `Fetching ${progress.completed}/${progress.total}…`;
      }
    }
    const workerCount = Math.min(BULK_FETCH_CONCURRENCY, feeds.length);
    await Promise.all(Array.from({ length: workerCount }, worker));

    const failedNote = progress.failed ? ` (${progress.failed} failed)` : "";
    button.textContent = `Fetched ${progress.completed}/${progress.total}${failedNote}`;
    button.classList.remove("primary");
    button.title = originalTitle;
    state.bulkFetch = null;
    progress = null;
    await loadFeeds();
    setTimeout(() => {
      button.textContent = original;
    }, 2500);
  });
}

/* ------------------------------------------------------------- selection */

async function selectFeed(ref) {
  state.selectedRef = ref;
  paneEl.hidden = false;
  handleEl.hidden = false;
  masterEl.classList.add("split-open");
  renderTable();

  const cached = state.feeds.find((feed) => feed.feed_ref === ref);
  if (cached) renderDetail(cached, null);
  try {
    const view = await api(`/api/scheduler/feeds/${ref}`);
    if (state.selectedRef === ref) renderDetail(view.feed, view.events);
  } catch (error) {
    if (state.selectedRef === ref && !cached) {
      paneEl.replaceChildren(
        el("div", { class: "body empty-state" }, `Failed to load feed: ${error.message}`)
      );
    }
  }
}

function closeDetail() {
  state.selectedRef = null;
  paneEl.hidden = true;
  handleEl.hidden = true;
  masterEl.classList.remove("split-open");
  renderTable();
}

/* ------------------------------------------------------------- interaction */

handleEl.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  handleEl.setPointerCapture(event.pointerId);
  handleEl.classList.add("dragging");
  const masterRight = masterEl.getBoundingClientRect().right;

  function onMove(moveEvent) {
    const min = 320;
    const max = Math.max(min, masterEl.clientWidth - 380);
    const width = Math.min(max, Math.max(min, masterRight - moveEvent.clientX));
    masterEl.style.setProperty("--detail-width", `${width}px`);
  }
  function onUp(upEvent) {
    handleEl.releasePointerCapture(upEvent.pointerId);
    handleEl.classList.remove("dragging");
    handleEl.removeEventListener("pointermove", onMove);
    handleEl.removeEventListener("pointerup", onUp);
    const width = parseFloat(getComputedStyle(masterEl).getPropertyValue("--detail-width"));
    if (width) localStorage.setItem(DETAIL_WIDTH_KEY, String(Math.round(width)));
  }
  handleEl.addEventListener("pointermove", onMove);
  handleEl.addEventListener("pointerup", onUp);
});

let searchTimer = null;
document.getElementById("search").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderTable, SEARCH_DEBOUNCE_MS);
});

for (const [id, key] of [
  ["filter-platform", "platform"],
  ["filter-interval", "interval"],
  ["filter-status", "status"],
]) {
  document.getElementById(id).addEventListener("change", (event) => {
    state[key] = event.target.value;
    if (key === "platform") renderVendors();
    renderTable();
  });
}

wireFetchAllButton(document.getElementById("fetch-all"));
wireVendorToggle(document.getElementById("vendor-toggle"));

const runToggleEl = document.getElementById("run-toggle");
runToggleEl.addEventListener("click", () => toggleRun(runToggleEl));

/* -------------------------------------------------------------------- boot */

// Top-level await keeps the load event behind the first full render.
await loadOverview().catch(() => {
  document.getElementById("degraded-banner").hidden = false;
});
await loadFeeds().catch((error) => {
  emptyEl.hidden = false;
  emptyEl.textContent = isDegraded(error)
    ? "Inspector backend unavailable."
    : `Failed to load feeds: ${error.message}`;
});
setInterval(() => loadOverview().catch(() => {}), OVERVIEW_POLL_MS);
