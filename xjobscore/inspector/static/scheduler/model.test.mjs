import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTO_EXPAND_ROWS,
  autoExpand,
  dueInfo,
  formatRequestRate,
  groupFeeds,
  groupRollup,
  intervalError,
  intervalInput,
  intervalSeconds,
  jobsToday,
  matchesFeed,
  sortFeeds,
  summaryCounts,
} from "./model.mjs";

function feed(source, state, interval = 900, options = {}) {
  return {
    source,
    key: options.key ?? source,
    feed_ref: options.feedRef ?? `${source}:${options.key ?? source}`,
    company_name: options.name ?? source,
    policy: {
      interval_s: interval,
      paused_at: options.paused ? "2026-01-01T00:00:00Z" : null,
      last_poll_at: options.lastPoll ?? null,
      due_at: options.dueAt ?? null,
    },
    sched: { status: { state }, requests_per_day: 86_400 / interval },
    telemetry: options.telemetry ?? null,
    postings: options.postings ?? null,
    spark: options.spark ?? null,
  };
}

const ALL = { view: "all", search: "" };

test("groups order by failing count, then size, and sort rows inside each group", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const rows = [
    feed("ashby", "stale", 900, { key: "b", name: "Bravo", dueAt: "2026-08-22T11:00:00Z" }),
    feed("ashby", "stale", 900, { key: "a", name: "Alpha", dueAt: "2026-08-22T11:00:00Z" }),
    feed("ashby", "backing_off", 900, { key: "c", name: "Charlie", dueAt: "2026-08-22T12:20:00Z" }),
    feed("workable", "stale", 900, { key: "w1" }),
    feed("workable", "stale", 900, { key: "w2" }),
    feed("workable", "stale", 900, { key: "w3" }),
    feed("workable", "stale", 900, { key: "w4" }),
    feed("lever", "backing_off", 900, { key: "l1", dueAt: "2026-08-22T12:10:00Z" }),
    feed("lever", "backing_off", 900, { key: "l2", dueAt: "2026-08-22T12:05:00Z" }),
  ];
  const groups = groupFeeds(rows, ALL, undefined, now);
  // lever has the most failing feeds; ashby beats workable on failing count despite being smaller.
  assert.deepEqual(groups.map((group) => group.source), ["lever", "ashby", "workable"]);
  assert.deepEqual(groups[1].rows.map((row) => row.key), ["a", "b", "c"]);
  assert.equal(groups[1].total, 3);
  const byName = groupFeeds(rows, ALL, { key: "name", direction: "desc" }, now);
  assert.deepEqual(byName.map((group) => group.source), ["lever", "ashby", "workable"]);
  assert.deepEqual(byName[1].rows.map((row) => row.key), ["c", "b", "a"]);
});

test("groups drop providers with no matching rows but keep the full total", () => {
  const rows = [
    feed("ashby", "backing_off", 900, { key: "a" }),
    feed("ashby", "stale", 900, { key: "b" }),
    feed("workable", "stale", 900, { key: "w" }),
  ];
  const groups = groupFeeds(rows, { view: "failing", search: "" });
  assert.deepEqual(groups.map((group) => group.source), ["ashby"]);
  assert.equal(groups[0].rows.length, 1);
  assert.equal(groups[0].total, 2);
  assert.deepEqual(groupFeeds(rows, { view: "all", search: "zzz" }), []);
});

test("group rollup reports worst state, counts by concern, sums, and soonest due", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const rows = [
    feed("ashby", "stale", 900, {
      key: "a", dueAt: "2026-08-22T11:00:00Z", lastPoll: "2026-08-21T10:00:00Z",
      spark: [1, 5], telemetry: { error_count_7d: 2 },
    }),
    feed("ashby", "stale", 3600, {
      key: "b", dueAt: "2026-08-22T11:30:00Z", lastPoll: "2026-08-21T11:00:00Z",
      spark: [0, 7], telemetry: { error_count_7d: 0 },
    }),
    feed("ashby", "backing_off", 900, {
      key: "c", dueAt: "2026-08-22T12:15:00Z", lastPoll: "2026-08-22T09:00:00Z",
      spark: [2, 1], telemetry: { error_count_7d: 14 },
    }),
    feed("ashby", "paused", 900, { key: "d", paused: true, dueAt: "2026-08-22T10:00:00Z" }),
  ];
  const rollup = groupRollup(rows, now);
  assert.equal(rollup.worst, "backing_off");
  assert.deepEqual(rollup.counts, [["backing_off", 1], ["stale", 2], ["paused", 1]]);
  assert.equal(rollup.failing, 1);
  assert.equal(rollup.jobs, 13);
  assert.equal(rollup.errors, 16);
  assert.equal(rollup.dueAt, "2026-08-22T11:00:00Z"); // paused row's earlier due_at is ignored
  assert.equal(rollup.lastPollAt, "2026-08-22T09:00:00.000Z");
  assert.deepEqual(rollup.intervals, [900, 3600]);
});

test("group rollup keeps sums unknown when no row carries the observation", () => {
  const rollup = groupRollup([feed("ashby", "never_polled"), feed("ashby", "never_polled", 900, { key: "b" })]);
  assert.equal(rollup.jobs, null);
  assert.equal(rollup.errors, null);
  assert.equal(rollup.dueAt, null);
  assert.equal(rollup.lastPollAt, null);
  assert.equal(rollup.worst, "never_polled");
});

test("auto-expand opens groups only for a narrowing filter with a small result", () => {
  const small = [{ rows: Array(AUTO_EXPAND_ROWS - 10) }, { rows: Array(10) }];
  const large = [{ rows: Array(AUTO_EXPAND_ROWS + 1) }];
  assert.equal(autoExpand(small, ALL), false);
  assert.equal(autoExpand(small, { view: "failing", search: "" }), true);
  assert.equal(autoExpand(small, { view: "all", search: "acme" }), true);
  assert.equal(autoExpand(large, { view: "failing", search: "" }), false);
});

test("summary counts map scheduler states to the header vocabulary", () => {
  const counts = summaryCounts({
    healthy: 1200, backing_off: 26, paused: 3, stale: 8, never_polled: 2,
  });
  assert.deepEqual(counts, { ok: 1200, erroring: 26, paused: 3, other: 10 });
  assert.deepEqual(summaryCounts(null), { ok: 0, erroring: 0, paused: 0, other: 0 });
});

test("due info counts down, flags imminent polls, and parks paused feeds", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const soon = dueInfo("2026-08-22T12:00:30Z", null, now);
  assert.equal(soon.text, "30s");
  assert.equal(soon.imminent, true);
  const later = dueInfo("2026-08-22T12:25:00Z", null, now);
  assert.equal(later.text, "25m");
  assert.equal(later.imminent, false);
  assert.equal(dueInfo("2026-08-22T18:30:00Z", null, now).text, "6.5h");
  const overdue = dueInfo("2026-08-22T11:00:00Z", null, now);
  assert.equal(overdue.text, "due");
  assert.equal(overdue.imminent, true);
  assert.deepEqual(dueInfo("2026-08-22T12:00:30Z", "2026-01-01T00:00:00Z", now),
    { sortKey: Infinity, text: "—", imminent: false });
  assert.deepEqual(dueInfo(null, null, now), { sortKey: Infinity, text: "—", imminent: false });
});

test("jobs today reads the newest daily spark bucket", () => {
  assert.equal(jobsToday(feed("a", "healthy", 900, { spark: [0, 3, 7] })), 7);
  assert.equal(jobsToday(feed("a", "healthy")), null);
  assert.equal(jobsToday(feed("a", "healthy", 900, { spark: [] })), null);
});

test("issues view includes only operational problems", () => {
  const filters = { view: "issues", provider: null, search: "" };
  assert.equal(matchesFeed(feed("a", "stale"), filters), true);
  assert.equal(matchesFeed(feed("a", "never_polled"), filters), true);
  assert.equal(matchesFeed(feed("a", "backing_off"), filters), true);
  assert.equal(matchesFeed(feed("a", "healthy"), filters), false);
  assert.equal(matchesFeed(feed("a", "paused"), filters), false);
});

test("failing view narrows to backing-off feeds only", () => {
  const filters = { view: "failing", provider: null, search: "" };
  assert.equal(matchesFeed(feed("a", "backing_off"), filters), true);
  assert.equal(matchesFeed(feed("a", "stale"), filters), false);
});

test("provider filter composes with view and search", () => {
  assert.equal(matchesFeed(feed("ashby", "healthy"), { view: "all", provider: "ashby", search: "" }), true);
  assert.equal(matchesFeed(feed("lever", "healthy"), { view: "all", provider: "ashby", search: "" }), false);
  assert.equal(
    matchesFeed(feed("ashby", "healthy", 900, { name: "Acme" }), { view: "all", provider: "ashby", search: "acme" }),
    true
  );
});

test("default due sort runs soonest-first with paused and unscheduled last", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const rows = sortFeeds([
    feed("later", "healthy", 900, { dueAt: "2026-08-22T13:00:00Z" }),
    feed("paused", "paused", 900, { paused: true, dueAt: "2026-08-22T12:01:00Z" }),
    feed("soon", "healthy", 900, { dueAt: "2026-08-22T12:05:00Z" }),
    feed("never", "never_polled", 900),
  ], undefined, now);
  assert.deepEqual(rows.map((row) => row.source), ["soon", "later", "never", "paused"]);
});

test("status sort puts problems first and pauses last", () => {
  const rows = sortFeeds([
    feed("paused", "paused"),
    feed("healthy", "healthy"),
    feed("backoff", "backing_off"),
    feed("stale", "stale"),
  ], { key: "status", direction: "asc" });
  assert.deepEqual(rows.map((row) => row.sched.status.state), [
    "backing_off", "stale", "healthy", "paused",
  ]);
});

test("jobs and errors sorts treat missing telemetry as smallest", () => {
  const withJobs = feed("a", "healthy", 900, { spark: [5] });
  const noJobs = feed("b", "healthy");
  assert.deepEqual(
    sortFeeds([noJobs, withJobs], { key: "jobs", direction: "desc" }).map((row) => row.source),
    ["a", "b"]
  );
  const withErrors = feed("c", "healthy", 900, { telemetry: { error_count_7d: 3 } });
  assert.deepEqual(
    sortFeeds([noJobs, withErrors], { key: "errors", direction: "desc" }).map((row) => row.source),
    ["c", "b"]
  );
});

test("custom intervals convert, normalize, and validate at policy bounds", () => {
  assert.equal(intervalSeconds("90", "minutes"), 5400);
  assert.deepEqual(intervalInput(5400), { value: 90, unit: "minutes" });
  assert.equal(intervalError(60), null);
  assert.equal(intervalError(7 * 86_400), null);
  assert.match(intervalError(59), /at least/);
  assert.match(intervalError(7 * 86_400 + 1), /exceed/);
});

test("request rates remain legible below one request per day", () => {
  assert.equal(formatRequestRate(96), "96");
  assert.equal(formatRequestRate(1.5), "1.5");
  assert.equal(formatRequestRate(1 / 7), "0.14");
  assert.equal(formatRequestRate(null), "—");
});
