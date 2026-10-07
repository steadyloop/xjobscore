"""Ask one posting's origin URL whether it is still there.

The listing tells us a posting stopped appearing. That is a *trigger*, never a
verdict: boards briefly empty out, feeds truncate, tokens die, and a whole
board's worth of postings can look absent at once for reasons that have nothing
to do with hiring. `records.Posting` says so outright -- absence from a listing
is not closure. This module supplies the evidence that is: a fetch of the
posting's own URL, and what the origin said.

Four outcomes preserve the closure channel: `absent` for dead links,
`page_closed` for explicit page messages or redirects, `live`, and
`inconclusive`. A refused request never supplies closure evidence.

Deliberately not the harvest detail leg. `json-api.py` catches `NetError`
without reading `.status`, so a 404 and a timeout are indistinguishable there --
correct for a harvest, which only wants to know it lost a row, and useless here.
Deliberately not `keyscan.probe` either: that asks whether a *board* exists,
bypasses `Net`, and retries with no backoff.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal
from urllib.parse import urldefrag

from xjobscore.net import Net, NetError
from xjobscore.records import LINK_DEAD, PAGE_CLOSED, Item, Posting

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["Outcome", "Probe", "parse_tombstone", "probe", "probe_many", "tombstone"]

# `absent` and `page_closed` are independent kinds of closure evidence.
# `inconclusive` is the absence of evidence, and must never advance closure
# state -- it records that we looked, and nothing more.
Outcome = Literal["live", "absent", "page_closed", "inconclusive"]

# A final answer from the origin: this identifier resolves to nothing. 410 is
# the stronger statement of the two, and both are recorded verbatim in
# `status` so a later analysis can weigh them differently.
_ABSENT_STATUS = {404, 410}

# Explicit messages observed on job pages. Inspect rendered text rather than
# JavaScript bundles, which commonly contain unused closed-job translations.
_CLOSED_MESSAGE = re.compile(
    r"\b(?:this|the) (?:job(?: posting)?|position|role|opportunity) "
    r"(?:(?:is|has been) (?:closed|filled|removed|no longer (?:active|available))"
    r"|(?:has )?expired)\b"
    r"|\b(?:we are |we're )?no longer accepting applications\b"
    r"|\bapplications for this (?:job|position|role) (?:are|have been) closed\b",
    re.IGNORECASE,
)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data) -> None:
        if not self.hidden:
            self.parts.append(data)


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


async def probe(net: Net, url: str, *, concurrency: int = 1, page: bool = False) -> Probe:
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
    if not 200 <= response.status < 300:
        return Probe("inconclusive", url=url, final_url=response.url, status=response.status)
    if page:
        # Product policy: a missing job whose page redirects is page-closed.
        # URL fragments and a trailing slash alone are not a different page.
        requested = urldefrag(url)[0].rstrip("/")
        landed = urldefrag(response.url)[0].rstrip("/")
        if requested != landed:
            return Probe(
                "page_closed", url=url, final_url=response.url,
                status=response.status, detail="job page redirected",
            )
        visible = _VisibleText()
        visible.feed(response.text)
        text = " ".join(" ".join(visible.parts).split())
        match = next((m for m in _CLOSED_MESSAGE.finditer(text) if not re.search(
            r"\b(?:until|when|if|once|before|unless)\s*$", text[max(0, m.start()-30):m.start()], re.I
        )), None)
        if match:
            return Probe(
                "page_closed", url=url, final_url=response.url,
                status=response.status, detail=match.group(0),
            )
        # A JS-only shell supplies no evidence that the job is still live.
        if not text or text.lower() in {"jobs", "careers"}:
            return Probe(
                "inconclusive", url=url, final_url=response.url,
                status=response.status, detail="job page requires rendering",
            )
    return Probe("live", url=url, final_url=response.url, status=response.status)


async def probe_many(
    net: Net, urls: Sequence[str], *, concurrency: int = 4, page: bool = False
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
            *(probe(net, url, concurrency=concurrency, page=page) for url in urls)
        )
    )


def tombstone(result: Probe, *, probed_at: datetime) -> dict:
    """The bronze payload for a confirmed link or page closure.

    Shaped as a payload rather than written straight to silver so the closure
    stays replayable like every other fact in the pipeline: `raw_posting` is
    content-addressed, so re-probing an already-tombstoned posting inserts
    nothing, and a parser fix re-derives the closure without re-fetching.
    """
    return {"_probe": {**result.evidence, "probed_at": probed_at.isoformat()}}


def parse_tombstone(item: Item, evidence: dict) -> list[Posting]:
    """Replay the same narrow closure fact for every source adapter."""
    kind = {"absent": LINK_DEAD, "page_closed": PAGE_CLOSED}.get(evidence.get("outcome"))
    if kind is None:
        return []
    return [Posting(
        external_id=item.external_id, title="", url_posting=evidence.get("url") or "",
        state="closed", closed_evidence=kind,
    )]
