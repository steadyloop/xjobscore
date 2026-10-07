import assert from "node:assert/strict";
import test from "node:test";

import { heartbeatPresentation, tokenPromotable } from "./model.mjs";

test("heartbeat states have quiet, explicit labels", () => {
  assert.deepEqual(heartbeatPresentation("healthy"), {
    label: "Healthy",
    tone: "healthy",
  });
  assert.equal(heartbeatPresentation("attention").label, "Attention");
  assert.equal(heartbeatPresentation("paused").label, "Paused");
  assert.equal(heartbeatPresentation("unexpected").label, "Offline");
});

test("only live unmuted feed-absent ATS tokens are promotable", () => {
  const eligible = {
    status: "live",
    muted: false,
    feed_state: "none",
    category: "ATS",
  };
  assert.equal(tokenPromotable(eligible), true);
  assert.equal(tokenPromotable({ ...eligible, polling_supported: false }), false);
  assert.equal(tokenPromotable({ ...eligible, status: "unknown" }), false);
  assert.equal(tokenPromotable({ ...eligible, muted: true }), false);
  assert.equal(tokenPromotable({ ...eligible, feed_state: "disabled" }), false);
  assert.equal(tokenPromotable({ ...eligible, category: "Direct" }), false);
});

import { clock, compactCount, shortDuration, sweepHeadline, sweepRate, sweepVendorRows } from "./model.mjs";

test("sweep numbers stay short", () => {
  assert.equal(compactCount(950), "950");
  assert.equal(compactCount(9812), "9.8k");
  assert.equal(compactCount(7000), "7k");
  assert.equal(compactCount(274846), "275k");
  assert.equal(shortDuration(42), "42s");
  assert.equal(shortDuration(75), "1m 15s");
  assert.equal(shortDuration(907), "15m");
  assert.equal(shortDuration(3725), "1h 2m");
  assert.equal(clock(221), "3:41");
});

test("vendors needing attention come first, done collapses", () => {
  const rows = sweepVendorRows([
    { ats: "greenhouse", state: "done", total: 5, done: 5 },
    { ats: "paylocity", state: "running", total: 100, done: 10 },
    { ats: "adp", state: "running", total: 100, done: 80 },
    { ats: "oracle", state: "cooling", total: 9, done: 1, cooling_s: 40 },
    { ats: "workable", state: "cooling", total: 9, done: 1, cooling_s: 200 },
    { ats: "taleo", state: "failing", total: 9, done: 0 },
  ]);
  assert.deepEqual(rows.active.map((v) => v.ats), ["taleo", "oracle", "workable", "paylocity", "adp"]);
  assert.deepEqual(rows.done, ["greenhouse"]);
});

test("sweep headline", () => {
  const running = { status: "running", total: 200, done: 110, rate: 38.4, eta_s: 900 };
  assert.deepEqual(sweepHeadline(running), { pct: 55, text: "55% · 38/s · ~15m left" });
  assert.equal(sweepHeadline({ ...running, status: "paused" }).text, "55% · paused");
  assert.equal(sweepHeadline({ ...running, rate: 0.13 }).text, "55% · 0.13/s · ~15m left");
  assert.equal(sweepRate(0), "0/s");
  assert.equal(sweepRate(0.1), "0.1/s");
});
