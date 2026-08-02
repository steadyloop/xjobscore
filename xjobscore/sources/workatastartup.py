"""YC Work at a Startup -- session-authenticated, no unauthenticated path.

    POST https://www.workatastartup.com/companies/fetch

Payload sample: notebooks/nATS-payloads.md#5-yc-work-at-a-startup.

- Not solvable unauthenticated: an empty POST body returns 422, and
  GET /companies/fetch?page=1 302s to login. A session cookie is required.
- ycombinator.com/jobs returns 200 but exposes no JobPosting JSON-LD --
  not a usable fallback.
- credentialed: authenticate once, store the session cookie in feed.config,
  and treat the board as best-effort -- expect consecutive_failures to climb
  whenever the session expires.
- Pagination shape past login is unconfirmed; verify the page param once you
  have a working session before assuming `?page=N`.
"""

from __future__ import annotations

from xjobscore.net import Net
from xjobscore.records import Feed, Record


async def harvest(feed: Feed, net: Net) -> Record:
    raise NotImplementedError
