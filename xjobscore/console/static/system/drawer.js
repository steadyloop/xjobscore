/* Console page — the feed drawer.
 *
 * Opened from a feed row on the board. Shows the handful of facts worth a
 * click (last poll and result, strikes, new postings, open postings,
 * conditional GET), the interval presets, recent polls, and the feed's
 * actions: fetch, pause/resume, and the two removals (Mute, Drop).
 *
 * The board owns the rows; this module owns the drawer and calls back into
 * the board (`onChanged`) after any write so counts and lines refresh.
 */

import { api } from "/static/shared/api.js";
import { el, formatCount, formatInterval, relativeTime, statusChip } from "/static/shared/format.js";
import { dropFeed, muteFeed } from "/static/shared/feed-policy.js";
import { INTERVAL_PRESETS, STATE_LABEL } from "/static/system/model.mjs";

const panelEl = document.getElementById("detail-panel");
const removeDialogEl = document.getElementById("remove-dialog");

const drawer = {
  feed: null,
  events: null,
  selectedRef: null,
  trigger: null,
  token: 0,
  message: null,
  hooks: {
    connected: () => true,
    toast: () => {},
    onChanged: async () => {},
  },
};

export function initDrawer(hooks) {
  Object.assign(drawer.hooks, hooks);
}

export function selectedRef() {
  return drawer.selectedRef;
}

function exactTime(value) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Pause and disable are one column now: `held` is simply polling = false.
function isHeld(feed) {
  return feed.policy?.enabled === false;
}

function statusMark(status) {
  return el(
    "span",
    { class: "ops-st", "data-state": status.state, title: status.detail },
    STATE_LABEL[status.state] ?? status.state
  );
}

// One cell, not a word plus a number: a failed poll shows its status code,
// coloured -- the number IS the word. ok is spelled out, unchanged/partial
// share a checkmark (both succeeded), error gets the ballot X.
const EVENT_MARK = { ok: "ok", unchanged: "✓", partial: "✓", error: "✗" };
const EVENT_LABEL = { ok: "OK", unchanged: "Unchanged", partial: "Partial", error: "Error" };

function eventMark(status, statusCode) {
  return el(
    "span",
    { class: "ops-event-status" },
    el(
      "span",
      { class: "ops-event-mark", "data-status": status, title: EVENT_LABEL[status] ?? status },
      EVENT_MARK[status] ?? "–"
    ),
    statusCode ? el("span", { class: "ops-event-code" }, String(statusCode)) : null
  );
}

/* Fetch bypasses the queue and the runner, so there are no
   scheduler-running/runner-alive conditions to check. */
function fetchBlocked(feed) {
  if (!drawer.hooks.connected()) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres) return "Import this feed before fetching it.";
  if (isHeld(feed)) return "Resume this feed before fetching it now.";
  if (feed.policy?.hold_until && new Date(feed.policy.hold_until) > new Date()) {
    return "A poll of this feed is already running.";
  }
  return null;
}

/* Mute and Drop both delete the feed row; the only difference is whether the
   discovery token is muted so the heartbeat cannot promote it back. */
function removeBlocked(feed) {
  if (!drawer.hooks.connected()) return "PostgreSQL is unavailable.";
  if (!feed.policy?.in_postgres || !feed.policy?.feed_id) {
    return "Not in the feed table — nothing to remove.";
  }
  return null;
}

/* Muting writes token.muted, and only ATS feeds have a discovery token. */
function canMute(feed) {
  return feed.category === "ATS" && !removeBlocked(feed);
}

function muteBlocked(feed) {
  return removeBlocked(feed) ?? (canMute(feed) ? null : "No discovery token to mute.");
}

/* Feed-level outcome messaging: inline when the feed is on display, a toast
   otherwise. */
function report(feed, message, tone = "ok") {
  if (drawer.selectedRef === feed.feed_ref) {
    drawer.message = message;
    render();
  } else {
    drawer.hooks.toast(message, tone);
  }
}

async function afterWrite(feed) {
  await drawer.hooks.onChanged();
  if (drawer.selectedRef === feed.feed_ref) await loadDetail();
}

async function runRemoval(feed, { rediscover }) {
  const label = feed.company_name || feed.key;
  try {
    // Muting is the atomic token-side mutation: it clears the feed row *and*
    // sets token.muted in one statement, so there is no drop-then-mute race.
    await (rediscover ? dropFeed(feed) : muteFeed(feed));
    if (drawer.selectedRef === feed.feed_ref) closePanel();
    await drawer.hooks.onChanged();
    drawer.hooks.toast(rediscover ? `${label} dropped; it can be rediscovered.` : `${label} muted and dropped.`);
  } catch (error) {
    report(feed, `Could not remove this feed: ${error.message}`, "error");
  }
}

/* Rediscovery on is what Drop means: the heartbeat re-probes the token once it
   reads dead and promotes the feed back. Mute is the one that stays gone. */
function openRemoveDialog(feed, { rediscover = false } = {}) {
  const label = feed.company_name || feed.key;
  const mutable = canMute(feed);
  const checkbox = el("input", { type: "checkbox", id: "remove-rediscover" });
  checkbox.checked = rediscover || !mutable;
  checkbox.disabled = !mutable;

  const verb = () => (checkbox.checked ? "Drop" : "Mute");
  const heading = el("h2", { id: "remove-title" }, `${verb()} ${label}?`);
  const confirmBtn = el("button", { class: "btn danger", type: "button" }, verb());
  checkbox.addEventListener("change", () => {
    heading.textContent = `${verb()} ${label}?`;
    confirmBtn.textContent = verb();
  });
  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Working…";
    removeDialogEl.close();
    await runRemoval(feed, { rediscover: checkbox.checked });
  });

  removeDialogEl.replaceChildren(
    el("header", { class: "ops-dialog-header" }, heading),
    el(
      "div",
      { class: "ops-dialog-body" },
      el("p", {}, `${feed.source}:${feed.key} leaves the fleet. Its poll history goes with it; job postings already harvested are kept.`),
      el(
        "label",
        { class: "ops-dialog-check" },
        checkbox,
        el(
          "span",
          {},
          "Let this board be rediscovered automatically",
          mutable ? null : el("span", { class: "muted" }, " — no discovery token to mute")
        )
      )
    ),
    el(
      "div",
      { class: "ops-dialog-footer" },
      el("button", { class: "btn", type: "button", onclick: () => removeDialogEl.close() }, "Cancel"),
      confirmBtn
    )
  );
  removeDialogEl.showModal();
}

async function applyInterval(feed, seconds, button) {
  button.disabled = true;
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { interval_s: seconds } });
    drawer.hooks.toast(`${feed.company_name || feed.key} polls every ${formatInterval(seconds)}.`);
    await afterWrite(feed);
  } catch (error) {
    report(feed, `Could not change the interval: ${error.message}`, "error");
    button.disabled = false;
  }
}

async function setPaused(feed, paused, button) {
  button.disabled = true;
  try {
    await api(`/api/feeds/${feed.feed_ref}/policy`, { body: { enabled: !paused } });
    drawer.hooks.toast(`${feed.company_name || feed.key} ${paused ? "paused" : "resumed"}.`);
    await afterWrite(feed);
  } catch (error) {
    report(feed, `Could not ${paused ? "pause" : "resume"} this feed: ${error.message}`, "error");
    button.disabled = false;
  }
}

/* Production work always enters the scheduler queue; progress then appears
   in the log below the board. */
async function fetchFeedNow(feed, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "queueing…";
  try {
    await api(`/api/scheduler/feeds/${feed.feed_ref}/fetch`, { body: {} });
    drawer.hooks.toast(`${feed.company_name || feed.key} queued for the unified pipeline.`);
    await afterWrite(feed);
  } catch (error) {
    report(feed, `Could not fetch this feed: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function fact(label, value, title = null) {
  return [el("dt", {}, label), el("dd", { title }, value)];
}

function render() {
  const feed = drawer.feed;
  if (!feed || feed.feed_ref !== drawer.selectedRef) return;
  const blocked = fetchBlocked(feed);
  const noRemove = removeBlocked(feed);
  const noMute = muteBlocked(feed);
  const interval = feed.policy?.interval_s;
  const writable = drawer.hooks.connected() && feed.policy?.in_postgres;
  const lastResult = feed.telemetry?.last_status
    ? statusChip(feed.telemetry.last_status)
    : el("span", { class: "muted" }, "—");

  panelEl.replaceChildren(
    el(
      "header",
      { class: "ops-panel-header" },
      el(
        "div",
        { class: "ops-panel-title" },
        el("div", { class: "ops-panel-title-row" }, statusMark(feed.sched.status), el("h2", { id: "panel-title" }, feed.company_name || feed.key)),
        el("p", {}, `${feed.source}:${feed.key}`)
      ),
      el("button", { class: "ops-close", type: "button", "aria-label": "Close feed details", onclick: closePanel }, "×")
    ),
    // replaceChildren() stringifies a bare null child, so spread a list.
    ...(drawer.message || blocked
      ? [el("div", { class: "ops-inline-message", role: "status" }, drawer.message ?? blocked)]
      : []),
    el(
      "section",
      { class: "ops-panel-section" },
      el(
        "dl",
        { class: "ops-facts" },
        fact("last poll", relativeTime(feed.policy?.last_poll_at), exactTime(feed.policy?.last_poll_at)),
        fact("last result", lastResult),
        fact("strikes", formatCount(feed.policy?.strikes)),
        fact("new · 7d", formatCount(feed.telemetry?.new_7d)),
        fact("postings", feed.postings?.open_postings == null ? "—" : `${formatCount(feed.postings.open_postings)} open`),
        fact("cond. get", feed.policy?.has_etag ? "etag" : "none")
      )
    ),
    el(
      "section",
      { class: "ops-panel-section" },
      el("h3", {}, "interval"),
      el(
        "div",
        { class: "ops-presets", role: "group", "aria-label": "Polling interval" },
        INTERVAL_PRESETS.map(([seconds, label]) =>
          el(
            "button",
            {
              type: "button",
              "aria-pressed": String(seconds === interval),
              disabled: !writable || seconds === interval ? "" : null,
              title: `Poll every ${formatInterval(seconds)}`,
              onclick: (event) => applyInterval(feed, seconds, event.currentTarget),
            },
            label
          )
        ),
        interval != null && !INTERVAL_PRESETS.some(([seconds]) => seconds === interval)
          ? el("span", { class: "muted" }, `now ${formatInterval(interval)}`)
          : null
      )
    ),
    el(
      "section",
      { class: "ops-panel-section" },
      el("h3", {}, "polls"),
      drawer.events?.length
        ? el(
            "ul",
            { class: "ops-events" },
            drawer.events.map((event) =>
              el(
                "li",
                {},
                el("time", { title: exactTime(event.polled_at) }, relativeTime(event.polled_at)),
                eventMark(event.status, event.status_code),
                el("span", { class: "ops-event-note" }, event.new_postings ? `${formatCount(event.new_postings)} new` : "—")
              )
            )
          )
        : el("div", { class: "muted" }, drawer.events ? "No poll observations recorded yet." : "Loading…")
    ),
    el(
      "footer",
      { class: "ops-panel-actions" },
      el(
        "button",
        {
          class: "btn",
          type: "button",
          disabled: blocked ? "" : null,
          title: blocked ?? "Fetch this feed right now",
          onclick: (event) => fetchFeedNow(feed, event.currentTarget),
        },
        "Fetch"
      ),
      el(
        "button",
        {
          class: "btn",
          type: "button",
          disabled: writable ? null : "",
          onclick: (event) => setPaused(feed, !isHeld(feed), event.currentTarget),
        },
        isHeld(feed) ? "Resume" : "Pause"
      ),
      el("span", { class: "ops-panel-spacer" }),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noMute ? "" : null,
          title: noMute ?? "Drop this feed and mute its token so it cannot come back",
          onclick: () => openRemoveDialog(feed, { rediscover: false }),
        },
        "Mute"
      ),
      el(
        "button",
        {
          class: "btn ops-panel-danger",
          type: "button",
          disabled: noRemove ? "" : null,
          title: noRemove ?? "Drop this feed from the fleet; it can be rediscovered",
          onclick: () => openRemoveDialog(feed, { rediscover: true }),
        },
        "Drop"
      )
    )
  );
}

/* Re-reads the open feed. The vendor hint keeps the lookup to one vendor. */
export async function loadDetail() {
  const ref = drawer.selectedRef;
  if (!ref) return;
  const token = ++drawer.token;
  const source = drawer.feed?.source;
  try {
    const query = source ? `?source=${encodeURIComponent(source)}` : "";
    const view = await api(`/api/scheduler/feeds/${ref}${query}`);
    if (token !== drawer.token || drawer.selectedRef !== ref) return;
    drawer.feed = view.feed;
    drawer.events = view.events ?? [];
    render();
  } catch (error) {
    if (token === drawer.token && drawer.selectedRef === ref) {
      drawer.message = `Could not refresh this feed: ${error.message}`;
      drawer.events = drawer.events ?? [];
      render();
    }
  }
}

/* Marks the open feed's row, wherever the board currently draws it. */
export function syncSelectedRow() {
  for (const row of document.querySelectorAll("#sched-table tr[data-feed-ref]")) {
    row.setAttribute("aria-selected", String(row.dataset.feedRef === drawer.selectedRef));
  }
}

export function selectFeed(feed, trigger = null) {
  const changed = drawer.selectedRef !== feed.feed_ref;
  drawer.selectedRef = feed.feed_ref;
  drawer.feed = feed;
  drawer.trigger = trigger ?? drawer.trigger;
  drawer.message = null;
  if (changed) drawer.events = null;
  panelEl.hidden = false;
  syncSelectedRow();
  render();
  // The drawer overlays the page, so focus moves into it (Escape closes it).
  requestAnimationFrame(() => panelEl.querySelector(".ops-close")?.focus());
  loadDetail().catch(() => {});
}

export function closePanel() {
  drawer.token += 1;
  drawer.selectedRef = null;
  drawer.feed = null;
  drawer.events = null;
  drawer.message = null;
  panelEl.hidden = true;
  syncSelectedRow();
  drawer.trigger?.focus();
  drawer.trigger = null;
}

/* Called when the run switch or connection changes, so disabled states follow. */
export function rerenderDrawer() {
  render();
}
