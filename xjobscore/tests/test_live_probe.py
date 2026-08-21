"""Drift canary: does Greenhouse still answer a dead posting with a 404?

The whole closure path rests on one assumption about one vendor. If Greenhouse
ever starts serving a soft-404 -- 200 with a "this job is no longer available"
body, or a redirect to the board index -- every probe comes back `live` and
closures silently stop. Nothing else in the suite would notice: the unit tests
assert we classify a 404 correctly, not that a 404 is still what arrives.

Not part of the default suite. A board going quiet is not a code defect, so
this runs only under XJOBSCORE_LIVE=1, per pyproject's `live` marker.
"""

from __future__ import annotations

import os

import pytest

from xjobscore import probe
from xjobscore.net import Net

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("XJOBSCORE_LIVE") != "1",
        reason="hits the real Greenhouse API; set XJOBSCORE_LIVE=1",
    ),
]

BOARD = "anthropic"
LIST_URL = f"https://boards-api.greenhouse.io/v1/boards/{BOARD}/jobs"
PROBE_URL = LIST_URL + "/{id}"

# Observed 404 on 2026-08-20, having been listed on 2026-08-17. A delisted
# Greenhouse id is never reissued, so this stays dead; the live half of the
# check is read from the board at run time instead of pinned, because any id
# hardcoded as live is only a matter of time from becoming a false failure.
DEAD_ID = "4983646008"


@pytest.mark.asyncio
async def test_a_delisted_posting_still_answers_404():
    async with Net(user_agent="xjobscore-test/0.1") as net:
        result = await probe.probe(net, PROBE_URL.format(id=DEAD_ID))

    assert result.outcome == "absent", (
        f"Greenhouse answered {result.status} for a known-dead posting. "
        "If this is a soft-404, the probe channel is silently closing nothing."
    )
    assert result.status == 404


@pytest.mark.asyncio
async def test_a_currently_listed_posting_answers_200():
    """The other half: a 404 for everything would close the entire board."""
    async with Net(user_agent="xjobscore-test/0.1") as net:
        listing = await net.json(LIST_URL)
        jobs = listing.get("jobs") or []
        assert jobs, f"{BOARD} listed no jobs; cannot check the live half"

        result = await probe.probe(net, PROBE_URL.format(id=str(jobs[0]["id"])))

    assert result.outcome == "live"
    assert result.status == 200
