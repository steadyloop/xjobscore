const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {
  sourceLabel,
  sourceMark,
  boardLabel,
  healthKey,
  needsAttention,
  summarize,
  relativeTime,
  withTimeout,
} = require("./app.js");

test("source presentation stays compact and readable", () => {
  assert.equal(sourceLabel("smart_recruiters"), "Smart Recruiters");
  assert.equal(sourceMark("greenhouse"), "GR");
  assert.equal(boardLabel("ATS", 1), "1 board");
  assert.equal(boardLabel("ATS", 2), "2 boards");
  assert.equal(boardLabel("Web Board", 1), "");
});

test("every backend health state has a stable visual key", () => {
  assert.equal(healthKey("Healthy"), "healthy");
  assert.equal(healthKey("Partial"), "partial");
  assert.equal(healthKey("Needs attention"), "needs-attention");
  assert.equal(healthKey("Not polled"), "not-polled");
  assert.equal(healthKey("Disabled"), "disabled");
  assert.equal(healthKey("Unexpected"), "unknown");
  assert.equal(needsAttention({ health: "Partial" }), true);
  assert.equal(needsAttention({ health: "Disabled" }), false);
});

test("summary uses only existing source coverage response fields", () => {
  const summary = summarize({
    sources: [{ health: "Healthy" }, { health: "Needs attention" }],
    source_count: 2,
    total_coverage: 428,
    total_added_today: 31,
  });
  assert.equal(summary.sourceCount, 2);
  assert.equal(summary.totalCoverage, 428);
  assert.equal(summary.totalAddedToday, 31);
  assert.equal(summary.attentionCount, 1);
});

test("polling freshness has concise labels", () => {
  const now = new Date("2026-08-09T12:00:00Z");
  assert.equal(relativeTime("2026-08-09T11:56:00Z", now), "4m ago");
  assert.equal(relativeTime("2026-08-08T12:00:00Z", now), "1d ago");
  assert.equal(relativeTime(null, now), "Never polled");
});

test("source coverage loading cannot remain pending indefinitely", async () => {
  await assert.rejects(withTimeout(new Promise(() => {}), 5), /request timed out/i);
});

test("source coverage is the canonical read-only page", () => {
  const page = fs.readFileSync(require.resolve("./index.html"), "utf8");
  const mainNavigation = fs.readFileSync(require.resolve("../index.html"), "utf8");
  assert.match(page, /<title>Source coverage · OneShot<\/title>/);
  assert.match(page, /id="coverage-content"/);
  assert.doesNotMatch(page, /type="search"|<select|data-filter|data-sort/);
  assert.match(mainNavigation, /href="\/static\/source-coverage\/index\.html"/);
  assert.match(page, /navigation\.css\?v=stable-nav-/);
});
