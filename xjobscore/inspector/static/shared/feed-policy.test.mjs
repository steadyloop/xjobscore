import assert from "node:assert/strict";
import test from "node:test";

import { muteFeed } from "./feed-policy.js";

const FEED = {
  source: "ashby",
  key: "acme",
  policy: { in_postgres: true, feed_id: 42 },
};

test("mute delegates the atomic token and feed mutation to one endpoint", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, method: init.method, body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ muted: true, feed_dropped: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await muteFeed(FEED);

  assert.deepEqual(requests, [
    {
      path: "/api/tokens/muted",
      method: "PUT",
      body: { ats: "ashby", token: "acme", muted: true },
    },
  ]);
});

test("a failed mute is surfaced from the single endpoint", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, method: init.method });
    return new Response(JSON.stringify({ detail: "no token" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  };

  await assert.rejects(() => muteFeed(FEED), /no token/);
  assert.deepEqual(requests, [
    { path: "/api/tokens/muted", method: "PUT" },
  ]);
});
