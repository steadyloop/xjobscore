/* Enable/disable one feed. Feeds page owns this end to end: enabling a
   catalog-only feed imports it into Postgres first, matching what the
   detail-pane switch has always done. */

import { api } from "./api.js";

export async function setFeedEnabled(feed, enabled) {
  if (feed.policy?.in_postgres) {
    return api(`/api/feeds/${feed.feed_ref}/policy`, { body: { enabled } });
  }
  if (enabled) {
    return api(`/api/feeds/${feed.feed_ref}/import`, { body: { enabled: true } });
  }
  throw new Error("feed was never imported");
}

/* Whole-vendor flip. One request, not one per feed: a vendor group is 2,591
   feeds for Ashby, and the backend imports the catalog-only ones on enable
   exactly as the single-row switch above does. */
export async function setFeedsEnabled(feeds, enabled) {
  const refs = feeds.map((feed) => feed.feed_ref).filter(Boolean);
  if (!refs.length) return { affected: 0 };
  return api("/api/feeds/bulk-policy", { body: { refs, enabled } });
}

/* Scheduler provider control resolves the complete membership on the server,
   avoiding both stale browser subsets and a full Console inventory read. */
export async function setProviderPaused(source, paused) {
  return api(`/api/scheduler/providers/${encodeURIComponent(source)}/pause`, {
    body: { paused },
  });
}

/* The token endpoint owns the whole mutation: muted=true and feed deletion
   happen atomically in PostgreSQL. */
export async function muteFeed(feed) {
  if (!feed.policy?.in_postgres || !feed.policy?.feed_id) {
    throw new Error("feed was never imported");
  }
  return api("/api/tokens/muted", {
    method: "PUT",
    body: { ats: feed.source, token: feed.key, muted: true },
  });
}

/* Drop removes the feed without changing the token's muted state. The token
   remains eligible for a later explicit import or heartbeat promotion. */
export async function dropFeed(feed) {
  if (!feed.policy?.in_postgres || !feed.policy?.feed_id) {
    throw new Error("feed was never imported");
  }
  return api(`/api/feeds/${feed.feed_ref}`, { method: "DELETE" });
}
