"""Parse replayable bronze rows into schema.sql's ``job_posting`` projection."""

from __future__ import annotations

from html.parser import HTMLParser
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from xjobscore import router, sources, store
from xjobscore.location_normalizer import normalize_location
from xjobscore.records import LINK_DEAD, Feed, Item, Posting

if TYPE_CHECKING:
    from asyncpg import Connection

logger = logging.getLogger(__name__)

__all__ = ["Counts", "process", "replay"]


class _PlainText(HTMLParser):
    _BLOCKS = {"br", "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _PlainText()
    parser.feed(value)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    return text or None


def _remote(value: str | None) -> str | None:
    """'yes'/'hybrid' only, and only for an explicit statement. Never 'no':
    a stated on-site/not-remote signal is not reliable enough to assert with
    confidence, so it is left absent -- the same as no signal at all -- rather
    than recorded as a false negative.
    """
    normalized = re.sub(r"[^a-z]+", "_", (value or "").strip().lower()).strip("_")
    return {
        "remote": "yes",
        "hybrid": "hybrid",
    }.get(normalized)


# Sources with no structured remote field still often say it plainly in the
# location string ("New York, NY (Remote)"). Description text is deliberately
# excluded: sampling showed loose keyword matches there are too noisy (e.g. a
# posting whose "Work Model: Onsite" line sits next to an unrelated mention of
# "remote" elsewhere in the body).
_LOCATION_REMOTE = re.compile(r"\b(remote|hybrid)\b", re.IGNORECASE)


def _remote_from_location(location_raw: str | None) -> str | None:
    match = _LOCATION_REMOTE.search(location_raw or "")
    return _remote(match.group(1)) if match else None


def _normalized_locations(posting: Posting) -> tuple[str | None, list[dict[str, Any]]]:
    primary = posting.location_normalization_raw or posting.location_raw
    locations: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for value in (primary, *posting.location_normalization_extra):
        if not value:
            continue
        for location in normalize_location(value)["locations"]:
            key = tuple(location.get(field) for field in ("country", "state", "city"))
            if key not in seen:
                seen.add(key)
                locations.append(location)
    return primary, locations


def _projection(posting: Posting) -> dict[str, Any]:
    description = _plain_text(posting.description_html)
    location_input, locations = _normalized_locations(posting)
    attributes: dict[str, Any] = {"version": 1}
    remote = _remote(posting.workplace_type_raw) or _remote_from_location(
        posting.location_raw
    )
    if remote:
        attributes["remote"] = remote
    if posting.deadline_at is not None:
        attributes["deadline_at"] = posting.deadline_at.isoformat()
    return {
        "title": posting.title,
        "description": description,
        # A source may provide extra structured context specifically for
        # normalization. Store that exact input so the normalized locations
        # remain reproducible from the canonical row.
        "location_text": location_input,
        "source_url": posting.url_posting,
        "apply_url": posting.apply_url,
        "posted_at": posting.published_at,
        # Derived at parse time from vendor-specific evidence and carried
        # through verbatim -- silver classifies nothing. An adapter whose
        # vendor has not been transcribed yet leaves these at their defaults.
        "job_type": posting.job_type,
        "remote": posting.remote,
        "locations": locations,
        "attributes": attributes,
        "status": "closed"
        if posting.state == "closed" or posting.source_active is False
        else "active",
    }


# The exact set of columns inside schema.sql's STABLE (HASH) block. Parsed and
# enriched outputs are overwritten independently and never define source
# content identity. Deliberately omitted projection keys include:
#   status     -- monotonic in the upsert: it can only ever go to 'closed'.
#                 Hashing it would report a change on the active->active and
#                 closed->active transitions the stored row cannot reflect.
#   attributes -- deadline_at and a silver-derived remote signal. No gold step
#                 reads either (skills_input_hash and degree_fields_input_hash
#                 both hash `description` alone), and the upsert merges
#                 attributes in both CASE branches regardless of content_hash,
#                 so excluding it here costs no data -- only a churn signal
#                 that was never wanted.
#   job_type, remote, locations -- parse-time derivations. Parser improvements
#                 may replace them without pretending the source posting
#                 changed or invalidating unrelated Gold enrichment.
_HASHED_FIELDS = (
    "title", "description", "location_text", "source_url",
    "apply_url", "posted_at",
)


def _content_hash(values: dict[str, Any]) -> str:
    return router.encoded_hash({key: values.get(key) for key in _HASHED_FIELDS})


@dataclass(frozen=True, slots=True)
class Counts:
    postings: int = 0
    # Subset of ``postings``: rows this call inserted rather than revised. A
    # posting whose description was edited is a changed posting, not a new one,
    # and only this counter may reach ``poll_observation.new_postings``.
    created: int = 0
    unchanged: int = 0
    unresolved: int = 0
    unsupported: int = 0
    secondhand: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            postings=self.postings + other.postings,
            created=self.created + other.created,
            unchanged=self.unchanged + other.unchanged,
            unresolved=self.unresolved + other.unresolved,
            unsupported=self.unsupported + other.unsupported,
            secondhand=self.secondhand + other.secondhand,
        )


async def _company_id(
    conn: Connection,
    feed: Feed,
    posting: Posting,
    *,
    aliases: dict[str, int] | None = None,
) -> int | None:
    resolved = None
    if posting.company_raw:
        resolved = await store.resolve_company(conn, posting.company_raw, aliases=aliases)
    if resolved is not None:
        return resolved
    if feed.type in {"ATS", "Direct"}:
        return feed.company_id
    return None


async def process(
    conn: Connection,
    feed: Feed,
    stored: list[router.StoredRaw],
    envelope: dict,
    *,
    seen_at: datetime,
    dry_run: bool = False,
    aliases: dict[str, int] | None = None,
    touch_seen: bool = True,
) -> Counts:
    """Parse and project raw items. Parser exceptions intentionally propagate.

    ``touch_seen`` is false when the caller did not observe the live listing --
    a replay over stored payloads, or a probe of a single posting. Only a fetch
    that actually returned the posting may advance ``last_seen_at``.
    """
    parse = sources.parser(feed.source)
    total = Counts()
    source = store.source_instance(feed)
    for raw in stored:
        item = raw.item
        item_envelope = envelope
        if raw.data is not None:
            reconstructed, item_envelope = router.unwrap(raw.data)
            item = Item(
                external_id=raw.item.external_id,
                payload=reconstructed.payload,
                kind=reconstructed.kind,
            )
        canonicals = parse(feed, item, item_envelope)
        postings = []
        for canonical in canonicals:
            if not isinstance(canonical, Posting):
                total += Counts(unsupported=1)
            elif canonical.fidelity != "origin":
                total += Counts(secondhand=1)
            else:
                postings.append(canonical)
        for posting in postings:
            # A probed dead link annotates a row that already exists; it never
            # creates one and carries nothing to project. Routing it through the
            # canonical upsert would overwrite title, description, and URLs with
            # the empty fields a tombstone necessarily has.
            if posting.closed_evidence == LINK_DEAD:
                if dry_run:
                    total += Counts(postings=1)
                    continue
                closed = await store.close_posting_by_key(
                    conn,
                    source=source,
                    source_key=posting.external_id,
                    closed_at=seen_at,
                )
                total += Counts(postings=1) if closed else Counts(unchanged=1)
                continue
            company_id = await _company_id(conn, feed, posting, aliases=aliases)
            if company_id is None:
                logger.warning(
                    "unresolved company source=%s source_key=%s company_raw=%r",
                    source,
                    posting.external_id,
                    posting.company_raw,
                )
                total += Counts(unresolved=1)
                continue
            values = _projection(posting)
            if dry_run:
                total += Counts(postings=1)
                continue
            # vendor_closed_at is the stated channel only. No link_checked_at
            # here: a stated closure arrives with the listing, and that moment
            # is already last_seen_at -- link_checked_at belongs to the probe
            # channel alone, a fetch of the posting's own URL.
            closed = values["status"] == "closed"
            _, changed, created = await store.upsert_job_posting(
                conn,
                source=source,
                source_key=posting.external_id,
                company_id=company_id,
                values=values,
                content_hash=_content_hash(values),
                raw_id=raw.raw_id,
                seen_at=seen_at,
                touch_seen=touch_seen,
                vendor_closed_at=seen_at if closed else None,
            )
            total += (
                Counts(postings=1, created=1 if created else 0)
                if changed
                else Counts(unchanged=1)
            )
    return total


async def replay(
    conn: Connection,
    feed: Feed,
    *,
    seen_at: datetime,
    dry_run: bool = False,
    aliases: dict[str, int] | None = None,
) -> Counts:
    """Reparse each source key's latest bronze revision without scheduling.

    ``latest_raw`` returns every source key the feed has *ever* produced, not
    the ones it returns today, so this pass says nothing about what is still
    listed. It therefore never touches ``last_seen_at``: doing so would stamp
    every vanished posting as freshly seen and erase the absence signal.
    """
    total = Counts()
    sources.parser(feed.source)  # fail before writing any replayed rows
    source = store.source_instance(feed)
    for row in await store.latest_raw(conn, source):
        item, envelope = router.unwrap(row["data"])
        item = Item(external_id=row["source_key"], payload=item.payload, kind=item.kind)
        stored = router.StoredRaw(
            item=item,
            raw_id=row["id"],
            data_hash=row["data_hash"],
            created=False,
            data=row["data"],
        )
        total += await process(
            conn, feed, [stored], envelope, seen_at=seen_at, dry_run=dry_run,
            aliases=aliases, touch_seen=False,
        )
    return total
