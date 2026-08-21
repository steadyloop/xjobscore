"""Ask one posting's origin URL whether it is still there.

The listing tells us a posting stopped appearing. That is a *trigger*, never a
verdict: boards briefly empty out, feeds truncate, tokens die, and a whole
board's worth of postings can look absent at once for reasons that have nothing
to do with hiring. `records.Posting` says so outright -- absence from a listing
is not closure. This module supplies the evidence that is: a fetch of the
posting's own URL, and what the origin said.

Three outcomes, never two. `inconclusive` is the load-bearing one: a 429 read as
a 404 is a fabricated closure, and a fabricated closure is worse than a missed
one because nothing downstream ever revisits it.

Deliberately not the harvest detail leg. `json-api.py` catches `NetError`
without reading `.status`, so a 404 and a timeout are indistinguishable there --
correct for a harvest, which only wants to know it lost a row, and useless here.
Deliberately not `keyscan.probe` either: that asks whether a *board* exists,
bypasses `Net`, and retries with no backoff.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from xjobscore.net import Net, NetError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["Outcome", "Probe", "probe", "probe_many", "tombstone"]

# `absent` is evidence the posting is gone. `live` is evidence it is not.
# `inconclusive` is the absence of evidence, and must never advance closure
# state -- it records that we looked, and nothing more.
Outcome = Literal["live", "absent", "inconclusive"]

# A final answer from the origin: this identifier resolves to nothing. 410 is
# the stronger statement of the two, and both are recorded verbatim in
# `status` so a later analysis can weigh them differently.
_ABSENT_STATUS = {404, 410}


@dataclass(frozen=True, slots=True)
class Probe:
    """What one origin fetch found. Frozen: an observation, not a scratchpad."""

    outcome: Outcome
    url: str  # requested
    final_url: str | None = None  # after redirects; None when the request threw
    status: int | None = None  # None means transport failure, not a status code
    detail: str | None = None  # the NetError message, for an inconclusive result

    @property
    def evidence(self) -> dict:
        """The probe as a bronze payload records it. JSON-safe, no objects."""
        return {
            "outcome": self.outcome,
            "url": self.url,
            "final_url": self.final_url,
            "http_status": self.status,
            "detail": self.detail,
        }


async def probe(net: Net, url: str, *, concurrency: int = 1) -> Probe:
    """Fetch one posting URL and classify what came back.

    `Net` already draws the line this needs: it retries 429 and 5xx up to
    `max_retries` and raises on everything else, so a `NetError` carrying 404 or
    410 arrived on the first try and is the origin's settled answer. A NetError
    carrying 429/5xx means we exhausted retries; one carrying no status at all
    means the transport never completed. Neither says anything about the job.
    """
    try:
        response = await net.get(url, concurrency=concurrency)
    except NetError as exc:
        if exc.status in _ABSENT_STATUS:
            return Probe("absent", url=url, final_url=exc.url, status=exc.status)
        # 403 belongs here too: being blocked is a fact about us, not the job.
        return Probe(
            "inconclusive",
            url=url,
            final_url=exc.url,
            status=exc.status,
            detail=str(exc),
        )
    # A 2xx body may still say "this role is filled", and a redirect may have
    # landed on a careers index rather than a posting. Both are soft closures
    # and both need per-vendor body/URL rules to read; until those exist this
    # reports `live` rather than guessing, and `final_url` is stored so the
    # rules can be written against real data later.
    return Probe("live", url=url, final_url=response.url, status=response.status)


async def probe_many(
    net: Net, urls: Sequence[str], *, concurrency: int = 4
) -> list[Probe]:
    """Probe a batch, letting `Net` handle pacing.

    Every board of one ATS shares a hostname, so the default 1 request/second
    per host would serialize the whole pass. `concurrency` > 1 swaps that pacing
    for a bounded semaphore on the host; `_HostPace` still absorbs a 429 with a
    host-wide cooldown, so raising this trades throughput for politeness rather
    than discarding it.
    """
    return list(
        await asyncio.gather(
            *(probe(net, url, concurrency=concurrency) for url in urls)
        )
    )


def tombstone(result: Probe, *, probed_at: datetime) -> dict:
    """The bronze payload for a confirmed dead link.

    Shaped as a payload rather than written straight to silver so the closure
    stays replayable like every other fact in the pipeline: `raw_posting` is
    content-addressed, so re-probing an already-tombstoned posting inserts
    nothing, and a parser fix re-derives the closure without re-fetching.
    """
    return {"_probe": {**result.evidence, "probed_at": probed_at.isoformat()}}
