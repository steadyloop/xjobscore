import assert from "node:assert/strict";
import test from "node:test";

import { muteFeed, setProviderPaused } from "./feed-policy.js";

const FEED = {
  source: "ashby",
  key: "acme",
  policy: { in_postgres: true, feed_id: 42 },
};

test("provider pause and resume each use one request without enumerating feeds", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init.body) });
    return Response.json({ affected: 2013, unknown: [] });
  };

  await setProviderPaused("paylocity", true);
  await setProviderPaused("paylocity", false);

  assert.equal(requests.length, 2);
  for (const [i, request] of requests.entries()) {
    assert.equal(request.path, "/api/scheduler/providers/paylocity/pause");
    assert.deepEqual(request.body, { paused: !i });
  }
});

test("bulk pause surfaces a failed request instead of reporting success", async () => {
  globalThis.fetch = async () => Response.json({ detail: "database unavailable" }, { status: 503 });
  await assert.rejects(
    () => setProviderPaused("paylocity", true),
    /database unavailable/
  );
});

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
