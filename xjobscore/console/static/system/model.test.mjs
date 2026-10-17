import assert from "node:assert/strict";
import test from "node:test";

import {
  STATE_LABEL,
  UNGROUPED,
  addSection,
  dropEmptySections,
  dueInfo,
  intervalRange,
  jobsToday,
  moveSource,
  pageWindow,
  parseSearch,
  providerRollup,
  renameSection,
  sectionCounts,
  sectionMatcher,
  sectionOf,
  sectionProviders,
  sortProviders,
  uniqueSectionName,
  viewMatches,
  visibleBuckets,
} from "./model.mjs";

function bucket(state, count, extra = {}) {
  return {
    state,
    count,
    interval_min: 900,
    interval_max: 900,
    due_min: null,
    last_min: null,
    last_max: null,
    jobs: null,
    errors: null,
    ...extra,
  };
}

function provider(source, buckets) {
  return {
    source,
    counts: Object.fromEntries(buckets.map((item) => [item.state, item.count])),
    buckets,
  };
}

const WORKDAY = provider("workday", [
  bucket("backing_off", 3, {
    interval_min: 900, interval_max: 3600, due_min: "2026-10-10T00:05:00+00:00",
    last_max: "2026-10-10T00:00:00+00:00", jobs: 0, errors: 14,
  }),
  bucket("stale", 2945, {
    interval_min: 21600, interval_max: 21600, due_min: "2026-10-10T00:01:00+00:00",
    last_max: "2026-10-08T00:00:00+00:00", jobs: 0, errors: 0,
  }),
  bucket("paused", 6, { interval_min: 900, interval_max: 900, jobs: 0, errors: 0 }),
  bucket("healthy", 3641, {
    interval_min: 900, interval_max: 21600, due_min: "2026-10-10T00:09:00+00:00",
    last_max: "2026-10-10T00:04:00+00:00", jobs: 31479, errors: 0,
  }),
]);

test("a vendor row rolls its status lines up into one set of columns", () => {
  assert.deepEqual(providerRollup(WORKDAY), {
    total: 6595,
    worst: "backing_off",
    intervalMin: 900,
    intervalMax: 21600,
    dueMin: "2026-10-10T00:01:00+00:00",
    lastMax: "2026-10-10T00:04:00+00:00",
    jobs: 31479,
    errors: 14,
  });
  // Without telemetry nothing carries jobs or errors: unknown, not zero.
  const blind = providerRollup(provider("x", [bucket("healthy", 2)]));
  assert.equal(blind.jobs, null);
  assert.equal(blind.errors, null);
  assert.equal(providerRollup(provider("y", [])).worst, null);
});

test("status lines appear worst first and follow the view pill", () => {
  const states = (view) => visibleBuckets(WORKDAY, view).map((item) => item.state);
  assert.deepEqual(states("all"), ["backing_off", "stale", "paused", "healthy"]);
  assert.deepEqual(states("issues"), ["backing_off", "stale"]);
  assert.deepEqual(states("failing"), ["backing_off"]);
  assert.deepEqual(states("paused"), ["paused"]);
  assert.equal(viewMatches("never_polled", "issues"), true);
  assert.equal(viewMatches("healthy", "issues"), false);
  assert.equal(STATE_LABEL.backing_off, "err");
  assert.equal(STATE_LABEL.never_polled, "new");
  assert.equal(STATE_LABEL.healthy, "ok");
});

test("vendors sort by any column with name breaking ties", () => {
  const quiet = provider("ashby", [bucket("healthy", 10, { jobs: 5, errors: 0 })]);
  const stale = provider("adp", [bucket("stale", 2, { jobs: 0, errors: 0 })]);
  const names = (sort) => sortProviders([quiet, WORKDAY, stale], sort).map((item) => item.source);
  assert.deepEqual(names({ key: "status", direction: "asc" }), ["workday", "adp", "ashby"]);
  assert.deepEqual(names({ key: "name", direction: "asc" }), ["adp", "ashby", "workday"]);
  assert.deepEqual(names({ key: "jobs", direction: "desc" }), ["workday", "ashby", "adp"]);
  assert.deepEqual(names({ key: "errors", direction: "desc" }), ["workday", "adp", "ashby"]);
});

test("interval ranges collapse when every feed shares one interval", () => {
  const short = (seconds) => `${seconds / 60}m`;
  assert.equal(intervalRange(900, 900, short), "15m");
  assert.equal(intervalRange(900, 3600, short), "15m–60m");
  assert.equal(intervalRange(null, null, short), "—");
});

test("pagers keep the ends and a window around the current page", () => {
  assert.deepEqual(pageWindow(1, 1), [1]);
  assert.deepEqual(pageWindow(1, 3), [1, 2, 3]);
  assert.deepEqual(pageWindow(1, 118), [1, 2, null, 118]);
  assert.deepEqual(pageWindow(50, 118), [1, null, 49, 50, 51, null, 118]);
  assert.deepEqual(pageWindow(118, 118), [1, null, 117, 118]);
});

test("due info counts down, flags imminent polls, and parks paused feeds", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const soon = dueInfo("2026-08-22T12:00:30Z", false, now);
  assert.equal(soon.text, "30s");
  assert.equal(soon.imminent, true);
  const later = dueInfo("2026-08-22T12:25:00Z", false, now);
  assert.equal(later.text, "25m");
  assert.equal(later.imminent, false);
  assert.equal(dueInfo("2026-08-22T18:30:00Z", false, now).text, "6.5h");
  const overdue = dueInfo("2026-08-22T11:00:00Z", false, now);
  assert.equal(overdue.text, "due");
  assert.equal(overdue.imminent, true);
  assert.deepEqual(dueInfo("2026-08-22T12:00:30Z", true, now),
    { sortKey: Infinity, text: "—", imminent: false });
  assert.deepEqual(dueInfo(null, false, now), { sortKey: Infinity, text: "—", imminent: false });
});

test("jobs today reads the newest daily spark bucket", () => {
  assert.equal(jobsToday({ spark: [0, 3, 7] }), 7);
  assert.equal(jobsToday({ spark: null }), null);
  assert.equal(jobsToday({ spark: [] }), null);
});

const LAYOUT = [
  { id: 1, name: "Watchlist", sources: ["workday", "lever"] },
  { id: 2, name: "Banks & Funds", sources: ["avature"] },
];

test("sections follow the layout order, Ungrouped last, empty ones dropped", () => {
  const providers = ["gem", "workday", "lever"].map((source) => provider(source, [bucket("healthy", 1)]));
  const sections = sectionProviders(providers, LAYOUT);
  assert.deepEqual(
    sections.map((section) => [section.name, section.providers.map((item) => item.source)]),
    [["Watchlist", ["workday", "lever"]], ["Ungrouped", ["gem"]]]
  );
  assert.equal(sections[1].ungrouped, true);
  assert.deepEqual(sectionCounts({ providers: [WORKDAY, provider("gem", [bucket("healthy", 4)])] }), [
    ["backing_off", 3], ["stale", 2945], ["paused", 6], ["healthy", 3645],
  ]);
});

test("group: in the search box narrows to sections by name, Ungrouped included", () => {
  assert.deepEqual(parseSearch('acme group:"banks & f" '), { text: "acme", group: "banks & f" });
  assert.deepEqual(parseSearch("group:watch"), { text: "", group: "watch" });
  assert.deepEqual(parseSearch("plain"), { text: "plain", group: null });

  assert.equal(sectionMatcher(LAYOUT, null), null);
  const watch = sectionMatcher(LAYOUT, "watch");
  assert.deepEqual(["workday", "avature", "gem"].map(watch), [true, false, false]);
  const loose = sectionMatcher(LAYOUT, "ungr");
  assert.deepEqual(["workday", "gem"].map(loose), [false, true]);
});

test("layout edits return a new layout and leave the old one for undo", () => {
  const moved = moveSource(LAYOUT, "lever", "2");
  assert.deepEqual(moved.map((group) => group.sources), [["workday"], ["avature", "lever"]]);
  assert.deepEqual(LAYOUT[0].sources, ["workday", "lever"]);
  assert.equal(sectionOf(moved, "lever"), "2");

  assert.deepEqual(moveSource(LAYOUT, "workday", UNGROUPED)[0].sources, ["lever"]);
  assert.equal(sectionOf(LAYOUT, "gem"), UNGROUPED);

  const added = addSection(LAYOUT, "Startups", ["lever"]);
  assert.deepEqual(added.at(-1), { id: null, name: "Startups", sources: ["lever"] });
  assert.deepEqual(added[0].sources, ["workday"]);

  assert.equal(renameSection(LAYOUT, "2", "Banks")[1].name, "Banks");
});

test("moving the last vendor out of a group removes the group", () => {
  const emptied = dropEmptySections(moveSource(LAYOUT, "avature", UNGROUPED));
  assert.deepEqual(emptied.map((group) => group.id), [1]);
});

test("new group names never collide, ignoring case", () => {
  assert.equal(uniqueSectionName(LAYOUT), "New group");
  const taken = [...LAYOUT, { id: 3, name: "new group", sources: [] }];
  assert.equal(uniqueSectionName(taken), "New group 2");
});
