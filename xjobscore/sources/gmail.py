"""Gmail job alerts -- the only pushed source; credentialed, secondhand.

    Gmail API users.messages.list?q={query}
    -> users.messages.get?format=full
    -> parse payload.parts[].body.data (base64url)

Payload sample: notebooks/nATS-payloads.md#4-gmail-job-alerts.

- credentialed: the query and the OAuth token live in feed.config. Failure on
  token expiry is expected and best-effort -- do not treat it as fatal to the
  source, just let the poll error normally.
- Prefer the text/html MIME part, fall back to text/plain. Each part's body
  is base64url-encoded.
- Extracted links are wrapped in tracking redirects; resolve them before
  building url_posting/origin_url or dedup breaks.
- `internalDate` is epoch milliseconds and is when the *email* arrived, not
  when the job posted -- never use it as published_at.
- Every posting here is a discovery hint, not a fact -- secondhand at best.
  Fidelity and whether it's worth a Target for the real ATS are the parsing
  layer's call, not this function's. Digests omit close dates and go stale
  within days.
"""

from __future__ import annotations

from xjobscore.net import Net
from xjobscore.records import Feed, Record


async def harvest(feed: Feed, net: Net) -> Record:
    raise NotImplementedError
