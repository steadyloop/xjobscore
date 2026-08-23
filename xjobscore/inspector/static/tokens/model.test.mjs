import assert from "node:assert/strict";
import test from "node:test";

import { heartbeatPresentation, tokenPromotable } from "./model.mjs";

test("heartbeat states have quiet, explicit labels", () => {
  assert.deepEqual(heartbeatPresentation("healthy"), {
    label: "Healthy",
    tone: "healthy",
  });
  assert.equal(heartbeatPresentation("attention").label, "Attention");
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
  assert.equal(tokenPromotable({ ...eligible, status: "unknown" }), false);
  assert.equal(tokenPromotable({ ...eligible, muted: true }), false);
  assert.equal(tokenPromotable({ ...eligible, feed_state: "disabled" }), false);
  assert.equal(tokenPromotable({ ...eligible, category: "Direct" }), false);
});
