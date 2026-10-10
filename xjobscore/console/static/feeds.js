/* Feeds page: fetch-and-inspect list, with per-row/per-vendor enable toggles.
   The full policy detail (interval, pause) still lives in the inspection
   panel (shared/feed-detail.js); this page only owns the on/off switch. */

import { api, isDegraded } from "/static/shared/api.js";
import { muteFeed, setFeedEnabled, setFeedsEnabled } from "/static/shared/feed-policy.js";
import {
  el,
  formatMs,
  relativeTime,
  statusChip,
} from "/static/shared/format.js";
import { createFeedDetail } from "/static/shared/feed-detail.js";
import { renderNav } from "/static/shared/nav.js";

const CATEGORIES = ["ATS", "Direct", "Board"];
const RENDER_CHUNK = 200;
const ARM_TIMEOUT_MS = 4000;
// Workers just bound in-flight requests from the browser; the backend's
// per-host lock and adaptive pacing (net.py _HostPace) is what actually
// keeps a single-host source like Ashby safe, no matter how many of these
// run concurrently. A same-host feed simply queues behind that lock.
const BULK_FETCH_CONCURRENCY = 8;

const state = {
  feeds: [],
  postgres: null,
  category: "ATS",
  search: "",
  enabledOnly: false,
  errorsOnly: false,
  expanded: new Set(),
  groupFetch: new Map(), // source -> {completed, total, failed}
  groupToggle: new Map(), // source -> feed count of an in-flight enable/disable
  selectedRef: null,
};

/* Inspection panel: list and payloads share the screen (master-detail). */
const masterEl = document.getElementById("master");
const paneEl = document.getElementById("detail-pane");
const handleEl = document.getElementById("split-handle");
const feedDetail = createFeedDetail(paneEl, {
  onClose: closeDetail,
  onMute: async () => {
    closeDetail();
    await load();
  },
  onDrop: async () => {
    closeDetail();
    await load();
  },
});

const LIST_WIDTH_KEY = "console-list-width";
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

async function selectFeed(ref) {
  const alreadyOpen = state.selectedRef === ref;
  state.selectedRef = ref;
  paneEl.hidden = false;
  handleEl.hidden = false;
  masterEl.classList.add("split-open");
  history.replaceState(null, "", `/feeds?ref=${ref}`);
  render();
  await (alreadyOpen ? feedDetail.refresh() : feedDetail.show(ref));
}

function closeDetail() {
  state.selectedRef = null;
  paneEl.hidden = true;
  handleEl.hidden = true;
  masterEl.classList.remove("split-open");
  history.replaceState(null, "", "/feeds");
  render();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.selectedRef) closeDetail();
});

renderNav("Feeds");
document.getElementById("search").addEventListener("input", (event) => {
  state.search = event.target.value.trim().toLowerCase();
  scheduleRender();
});
for (const [id, key] of [
  ["filter-enabled", "enabledOnly"],
  ["filter-errors", "errorsOnly"],
]) {
  document.getElementById(id).addEventListener("change", (event) => {
    state[key] = event.target.checked;
    render();
  });
}

let searchTimer = null;
function scheduleRender() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(render, 180);
}

/* Two-click arm, same reasoning as the scheduler page's Fetch-all: this is
   the one action on this page worth a second thought, since unlike a single
   row's Fetch it can mean thousands of live requests. Scope is whatever the
   current filters show, not the whole catalog -- narrow the filters first to
   narrow the run. While running, the button doubles as Stop. */
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
      const count = state.feeds.filter(matches).length;
      if (!count) return;
      armed = true;
      button.textContent = `Confirm — fetch ${count} feeds live`;
      button.classList.add("primary");
      timer = setTimeout(disarm, ARM_TIMEOUT_MS);
      return;
    }
    disarm();

    const feeds = state.feeds.filter(matches);
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
          feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
            body: {},
          });
        } catch {
          progress.failed += 1;
        }
        progress.completed += 1;
        button.textContent = progress.cancelled
          ? `Stopping… ${progress.completed}/${progress.total}`
          : `Fetching ${progress.completed}/${progress.total}…`;
        render();
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
    await load();
    setTimeout(() => {
      button.textContent = original;
    }, 2500);
  });
}
wireFetchAllButton(document.getElementById("fetch-all-visible"));

// Same two-click arm/confirm as Fetch-all. Enabling has one meaning everywhere
// on this page: import a feed into Postgres if needed, then switch it on. Use
// the bulk route so a broad visible selection remains one database round trip.
function enableCandidates() {
  return state.feeds
    .filter(matches)
    .filter((feed) => feed.policy && feed.feed_ref)
    .filter((feed) => !(feed.policy.in_postgres && feed.policy.enabled));
}

function wireEnableAllButton(button) {
  const original = button.textContent;
  let armed = false;
  let timer = null;
  let running = false;

  function disarm() {
    armed = false;
    button.textContent = original;
    button.classList.remove("primary");
    clearTimeout(timer);
  }

  button.addEventListener("click", async () => {
    if (running) return;
    if (!armed) {
      const count = enableCandidates().length;
      if (!count) return;
      armed = true;
      button.textContent = `Confirm — enable ${count} feeds`;
      button.classList.add("primary");
      timer = setTimeout(disarm, ARM_TIMEOUT_MS);
      return;
    }
    disarm();
    running = true;

    const feeds = enableCandidates();
    button.classList.add("primary");
    button.textContent = `Enabling ${feeds.length} feeds…`;
    let enabled = false;
    try {
      await setFeedsEnabled(feeds, true);
      for (const feed of feeds) {
        feed.policy = { ...feed.policy, in_postgres: true, enabled: true };
      }
      enabled = true;
      button.textContent = `Enabled ${feeds.length}/${feeds.length}`;
    } catch {
      button.textContent = "Enable failed";
    }
    button.classList.remove("primary");
    running = false;
    render();
    // Reconcile imported feeds with their new feed_id and schedule state.
    if (enabled) await load().catch(() => {});
    setTimeout(() => {
      button.textContent = original;
    }, 2500);
  });
}
wireEnableAllButton(document.getElementById("enable-all-visible"));

async function load() {
  const payload = await api("/api/feeds");
  state.feeds = payload.feeds.filter((feed) => feed.configured);
  state.postgres = payload.postgres;
  const empty = document.getElementById("feeds-empty");
  // A prior failed load may have replaced this text. Reset it before render
  // so recovered requests cannot leave an obsolete backend error beside rows.
  empty.textContent = "No feeds match the current filters.";
  empty.hidden = true;
  document.getElementById("degraded-banner").hidden = Boolean(
    payload.postgres?.connected
  );
  render();
}

function matches(feed) {
  if (state.category !== "All" && feed.category !== state.category) return false;
  if (state.enabledOnly && !(feed.policy && feed.policy.enabled)) return false;
  if (state.errorsOnly && feed.last_result?.status !== "error") return false;
  if (state.search) {
    const haystack = `${feed.source} ${feed.key} ${feed.company_name ?? ""}`.toLowerCase();
    if (!haystack.includes(state.search)) return false;
  }
  return true;
}

function renderCategoryPills() {
  const counts = new Map(CATEGORIES.map((c) => [c, 0]));
  for (const feed of state.feeds) {
    counts.set(feed.category, (counts.get(feed.category) ?? 0) + 1);
  }
  document.getElementById("category-pills").replaceChildren(
    ...CATEGORIES.map((category) =>
      el(
        "button",
        {
          class: "pill",
          type: "button",
          "aria-pressed": String(state.category === category),
          onclick: () => {
            state.category = category;
            render();
          },
        },
        `${category} ${counts.get(category) ?? 0}`
      )
    )
  );
}

function lastRunCell(feed) {
  const last = feed.last_result;
  if (!last) return statusChip("never");
  const chip = statusChip(last.status);
  if (last.status === "error" && last.error_class) {
    chip.title = `${last.error_class}: ${last.error_detail ?? ""}`;
  }
  chip.title = chip.title || `Fetched ${relativeTime(last.finished_at)}`;
  return el(
    "span",
    { style: "display:inline-flex;align-items:center;gap:8px" },
    chip,
    el(
      "span",
      { class: "muted last-run-time", title: last.finished_at },
      relativeTime(last.finished_at)
    )
  );
}

function fetchButton(feed) {
  const button = el(
    "button",
    { class: "btn small", type: "button", title: "Fetch this feed now" },
    "Fetch"
  );
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    button.disabled = true;
    button.textContent = "…";
    try {
      feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
        body: {},
      });
    } catch (error) {
      feed.last_result = {
        status: "error",
        error_class: "FetchFailed",
        error_detail: error.message,
        item_count: 0,
        elapsed_ms: null,
        finished_at: new Date().toISOString(),
      };
    }
    button.disabled = false;
    button.textContent = "Fetch";
    // Fetch-and-inspect is the core loop: show the result immediately.
    await selectFeed(feed.feed_ref);
  });
  return button;
}

/* Mute removes the feed and prevents the token heartbeat from promoting it
   again. Direct/Board sources are out of scope, and a feed that was never
   imported has nothing to remove. */
function muteButton(feed) {
  const button = el(
    "button",
    { class: "btn small", type: "button", title: "Mute token and remove feed" },
    "Mute"
  );
  if (feed.category !== "ATS" || !feed.policy?.in_postgres) {
    button.disabled = true;
    button.title =
      feed.category !== "ATS"
        ? "Mute is ATS-only"
        : "Not imported into the feed table yet";
    return button;
  }
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    button.disabled = true;
    button.textContent = "…";
    try {
      await muteFeed(feed);
      if (state.selectedRef === feed.feed_ref) closeDetail();
      await load();
    } catch (error) {
      button.disabled = false;
      button.textContent = "Mute";
      console.error(error);
    }
  });
  return button;
}

function feedToggle(feed) {
  const input = el("input", { type: "checkbox" });
  if (feed.policy) {
    input.checked = Boolean(feed.policy.in_postgres && feed.policy.enabled);
    input.title = input.checked ? "Disable this feed" : "Enable this feed";
  } else {
    input.disabled = true;
    input.title = "PostgreSQL policy store unavailable";
  }
  input.addEventListener("change", async () => {
    const next = input.checked;
    input.disabled = true;
    try {
      await setFeedEnabled(feed, next);
      feed.policy = { ...feed.policy, in_postgres: true, enabled: next };
    } catch {
      input.checked = !next;
    } finally {
      input.disabled = false;
    }
  });
  return el("span", { class: "switch" }, input, el("span", { class: "track" }));
}

function feedRow(feed) {
  const row = el("tr", { class: "row-link" });
  if (feed.feed_ref === state.selectedRef) row.setAttribute("aria-selected", "true");
  row.addEventListener("click", () => {
    selectFeed(feed.feed_ref);
  });

  const fetchTd = el("td", {});
  fetchTd.addEventListener("click", (event) => event.stopPropagation());
  fetchTd.append(
    el(
      "span",
      { style: "display:inline-flex;align-items:center;gap:6px" },
      feedToggle(feed),
      fetchButton(feed),
      muteButton(feed)
    )
  );

  row.append(
    fetchTd,
    el(
      "td",
      { class: "feed-cell", title: `${feed.source}:${feed.key}` },
      el(
        "div",
        { class: "feed-name" },
        el("span", { class: "feed-display-name" }, feed.company_name || feed.key),
        el("span", { class: "feed-identity mono" }, `${feed.source}:${feed.key}`)
      )
    ),
    el("td", {}, lastRunCell(feed)),
    el("td", { class: "num" }, feed.last_result ? String(feed.last_result.item_count) : "—"),
    el("td", { class: "num" }, feed.last_result ? formatMs(feed.last_result.elapsed_ms) : "—")
  );
  return row;
}

function isEnabled(feed) {
  return Boolean(feed.policy?.in_postgres && feed.policy.enabled);
}

/* Whole-vendor switch. Scope is every feed in the group, not just the ones
   already in Postgres: enabling a catalog-only vendor is precisely how its
   feeds get imported, and gating on in_postgres made the switch dead on the
   one source where it matters most (2,591 of 2,626 feeds are Ashby, and none
   of them start out imported). One request covers the batch -- see
   /api/feeds/bulk-policy. */
function groupToggle(source, feeds) {
  const eligible = feeds.filter((feed) => feed.policy && feed.feed_ref);
  const enabledCount = eligible.filter(isEnabled).length;
  const busy = state.groupToggle.has(source);

  const input = el("input", { type: "checkbox" });
  input.disabled =
    eligible.length === 0 || busy || Boolean(state.groupFetch.get(source));
  input.checked = eligible.length > 0 && enabledCount === eligible.length;
  input.indeterminate = enabledCount > 0 && enabledCount < eligible.length;
  if (busy) {
    input.title = `Applying to ${eligible.length} feeds…`;
  } else if (!eligible.length) {
    input.title = "PostgreSQL policy store unavailable";
  } else {
    const verb = input.checked ? "disable" : "enable (importing as needed)";
    input.title = `${enabledCount}/${eligible.length} feeds enabled — click to ${verb} all`;
  }

  input.addEventListener("change", async (event) => {
    event.stopPropagation();
    const next = input.checked;
    const targets = eligible.filter(
      (feed) => Boolean(feed.policy.in_postgres && feed.policy.enabled) !== next
    );
    if (!targets.length) return;
    state.groupToggle.set(source, targets.length);
    render();
    let flipped = false;
    try {
      await setFeedsEnabled(targets, next);
      // Optimistic, so the switch settles immediately on a 2,591-feed vendor.
      for (const feed of targets) {
        feed.policy = { ...feed.policy, in_postgres: true, enabled: next };
      }
      flipped = true;
    } catch {
      // Left as-is; the toggle recomputes from feed.policy on next render.
    } finally {
      state.groupToggle.delete(source);
    }
    render();
    // Then reconcile: a freshly imported feed has a real feed_id, interval and
    // schedule row that the optimistic patch above cannot invent.
    if (flipped) await load().catch(() => {});
  });

  const wrap = el("span", { class: "switch" }, input, el("span", { class: "track" }));
  wrap.addEventListener("click", (event) => event.stopPropagation());
  return wrap;
}

function groupRow(source, feeds, expanded) {
  const progress = state.groupFetch.get(source);
  // Vendor fetch runs the enabled feeds only -- the disabled ones are off on
  // purpose, and a 2,591-feed vendor would otherwise fetch its whole catalog.
  const enabledFeeds = feeds.filter(isEnabled);
  const fetchLabel = progress
    ? `Fetching ${progress.completed}/${progress.total}…`
    : `Fetch ${enabledFeeds.length}`;
  const errors = feeds.filter((feed) => feed.last_result?.status === "error").length;
  const medianElapsed = median(
    feeds.map((feed) => feed.last_result?.elapsed_ms).filter((value) => value != null)
  );

  const fetchButton = el(
    "button",
    {
      class: "btn small",
      type: "button",
      onclick: (event) => {
        event.stopPropagation();
        fetchGroup(source, enabledFeeds);
      },
      title: enabledFeeds.length
        ? `Fetch the ${enabledFeeds.length} enabled feeds in this source`
        : "No enabled feeds in this source",
    },
    fetchLabel
  );
  if (progress || state.bulkFetch || !enabledFeeds.length) {
    fetchButton.disabled = true;
  }

  const row = el(
    "tr",
    { class: "group-row" },
    el(
      "td",
      { colspan: 4 },
      el(
        "span",
        { style: "display:flex;align-items:center;gap:12px" },
        groupToggle(source, feeds),
        fetchButton,
        el("span", {}, `${expanded ? "▾" : "▸"} ${source}`),
        state.groupToggle.has(source)
          ? el("span", { class: "badge" }, `applying to ${state.groupToggle.get(source)}…`)
          : null,
        errors
          ? el("span", { class: "badge", "data-kind": "error" }, `${errors} errors`)
          : null,
        progress?.failed
          ? el("span", { class: "badge", "data-kind": "error" }, `${progress.failed} failed`)
          : null
      )
    ),
    el(
      "td",
      { class: "num", title: "Median elapsed across last-run feeds in this source" },
      medianElapsed != null ? formatMs(medianElapsed) : "—"
    )
  );
  row.addEventListener("click", () => {
    if (expanded) state.expanded.delete(source);
    else state.expanded.add(source);
    render();
  });
  return row;
}

async function fetchGroup(source, feeds) {
  const progress = { completed: 0, total: feeds.length, failed: 0 };
  state.groupFetch.set(source, progress);
  render();
  // Sequential on purpose: the shared Net policy is per-process, and hammering
  // one host with parallel manual fetches would distort the timing data.
  for (const feed of feeds) {
    try {
      feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`, {
        body: {},
      });
    } catch {
      progress.failed += 1;
    }
    progress.completed += 1;
    render();
  }
  state.groupFetch.delete(source);
  await load();
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function feedSortKey(feed) {
  return (feed.company_name || feed.key || "").toLowerCase();
}

function render() {
  renderCategoryPills();
  const body = document.getElementById("feeds-body");
  const visible = state.feeds.filter(matches);
  document.getElementById("feeds-empty").hidden = visible.length > 0;

  const groups = new Map();
  for (const feed of visible) {
    if (!groups.has(feed.source)) groups.set(feed.source, []);
    groups.get(feed.source).push(feed);
  }

  // Two alphabetized blocks, not one interleaved sort: single-feed sources
  // (Direct/Board) first in name order, then ATS vendors in source order.
  const flatFeeds = [];
  const atsGroups = [];
  for (const [source, feeds] of groups) {
    if (feeds[0].category !== "ATS") flatFeeds.push(...feeds);
    else atsGroups.push([source, feeds]);
  }
  flatFeeds.sort((a, b) => feedSortKey(a).localeCompare(feedSortKey(b)));
  atsGroups.sort(([, a], [, b]) => b.length - a.length);

  body.replaceChildren();
  const work = [];
  // Direct and Board sources are single feeds by design — render them
  // flat, styled like group rows so they read as top-level sources.
  for (const feed of flatFeeds) {
    const row = feedRow(feed);
    row.classList.add("flat-source");
    body.append(row);
  }
  for (const [source, feeds] of atsGroups) {
    const expanded = state.expanded.has(source);
    body.append(groupRow(source, feeds, expanded));
    if (expanded) work.push({ anchor: body.lastElementChild, feeds });
  }

  // Chunked expansion keeps a 2,600-row source from freezing the tab.
  for (const { anchor, feeds } of work) {
    let index = 0;
    let cursor = anchor;
    const step = () => {
      if (!cursor.isConnected) return; // a re-render replaced this table body
      const fragment = document.createDocumentFragment();
      for (const feed of feeds.slice(index, index + RENDER_CHUNK)) {
        fragment.append(feedRow(feed));
      }
      const last = fragment.lastElementChild;
      cursor.after(fragment);
      if (last) cursor = last;
      index += RENDER_CHUNK;
      if (index < feeds.length) requestAnimationFrame(step);
    };
    step();
  }
}

// Top-level await: first render completes before the window load event,
// so there is no empty-table flash (and headless screenshots are stable).
await load().catch((error) => {
  document.getElementById("feeds-empty").hidden = false;
  document.getElementById("feeds-empty").textContent = isDegraded(error)
    ? "Console backend unavailable."
    : `Failed to load feeds: ${error.message}`;
});

// Deep link: /?ref=… reopens the panel on that feed.
const initialRef = new URLSearchParams(location.search).get("ref");
if (initialRef) await selectFeed(initialRef);
