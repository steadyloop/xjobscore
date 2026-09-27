"""One board in, its job titles out. Nothing else.

The probe endpoint each rule already declares returns the job list itself, so
a title read is that request minus the liveness ladder: one GET, no retries,
no exists-check, no name lookup, nothing persisted. Use :mod:`keyscan.probe`
when the question is "is this board real" instead.
"""

from __future__ import annotations

import httpx

from .core import LIST_KEYS
from .rules import BY_SOURCE

# Accept-Encoding is left to httpx: it advertises only what it can actually
# decode, and naming `br` here without brotli installed returns raw bytes.
HEADERS = {"User-Agent": "xjobscore-peek/0.1"}
# Vendors disagree on the field; lever calls the title `text` and keeps `name`
# for the team, so the specific names go first. Compared case-folded because
# oracle capitalises every key.
TITLE_KEYS = ("title", "text", "jobtitle", "positionname", "name")
# Two probe URLs answer a count but not a listing. Oracle asks for a single
# requisition because a count is all the probe wants; workable's bare widget
# is the account record (name, description) and only names the jobs when
# asked. Both are a rewrite of the probe URL, not a new endpoint.
PROBE_REWRITE = {
    "oracle": lambda url: url.replace("limit=1", "limit=200"),
    "workable": lambda url: url + ("&" if "?" in url else "?") + "details=true",
}
# Walk order, deduplicated once rather than rebuilt at every node. Oracle's
# envelope keys lead: its outer list holds no titles, so the walk must go on.
SEARCH_KEYS = tuple(dict.fromkeys(
    ("requisitionList", "jobPostings", "jobs", "postings", *LIST_KEYS, "meta")
))


def _title(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    folded = {k.lower(): v for k, v in item.items()}
    for key in TITLE_KEYS:
        if isinstance(value := folded.get(key), str) and value.strip():
            return value.strip()
    return ""


def titles(payload: object) -> list[str]:
    """Job titles anywhere in a board payload, whatever shape it arrived in."""
    if isinstance(payload, list):
        found = [t for t in map(_title, payload) if t]
        if found:
            return found
        # An envelope list: the requisitions hang off its members.
        return [t for item in payload for t in titles(item)]
    if isinstance(payload, dict):
        # No type check on the branch: anything but a list or dict recurses to [].
        for key in SEARCH_KEYS:
            if found := titles(payload.get(key)):
                return found
    return []


async def peek(ats: str, key: str, client: httpx.AsyncClient) -> list[str]:
    """Titles on ``ats``/``key``; empty when the board is gone or not JSON."""
    rule = BY_SOURCE[ats]
    url = rule.probe_url.format(**rule.urls(key))
    if rewrite := PROBE_REWRITE.get(ats):
        url = rewrite(url)
    r = await client.request(
        rule.method, url, json=rule.body, headers={**HEADERS, **(rule.headers or {})},
    )
    try:
        return titles(r.json()) if r.status_code == 200 else []
    except ValueError:  # HTML/XML boards answer 200 with no JSON body.
        return []
