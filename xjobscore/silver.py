"""Parse replayable bronze rows into schema.sql's ``job_posting`` projection."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from xjobscore import opportunity, router, sources, store
from xjobscore.records import Feed, Item, Posting

if TYPE_CHECKING:
    from asyncpg import Connection

logger = logging.getLogger(__name__)

__all__ = ["Counts", "normalize_title", "process", "replay"]

_CONFUSABLES = str.maketrans({"ꓟ": "M", "ꓡ": "L", "ꓣ": "R"})


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.translate(_CONFUSABLES)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


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


def _workplace(value: str | None) -> str | None:
    normalized = re.sub(r"[^a-z]+", "_", (value or "").strip().lower()).strip("_")
    return {
        "remote": "remote",
        "hybrid": "hybrid",
        "on_site": "on_site",
        "onsite": "on_site",
    }.get(normalized)


def _projection(posting: Posting) -> dict[str, Any]:
    description = _plain_text(posting.description_html)
    attributes: dict[str, Any] = {
        "version": 1,
        "opportunity_type": opportunity.classify(posting.title, description),
    }
    workplace = _workplace(posting.workplace_type_raw)
    if workplace:
        attributes["workplace"] = workplace
    if posting.deadline_at is not None:
        attributes["deadline_at"] = posting.deadline_at.isoformat()
    return {
        "title": posting.title,
        "description": description,
        "location_text": posting.location_raw,
        "source_url": posting.url_posting,
        "apply_url": posting.apply_url,
        "posted_at": posting.published_at,
        "attributes": attributes,
        "status": "closed"
        if posting.state == "closed" or posting.source_active is False
        else "active",
    }


def _content_hash(values: dict[str, Any]) -> str:
    encoded = json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


async def _company_id(conn: Connection, feed: Feed, posting: Posting) -> int | None:
    resolved = None
    if posting.company_raw:
        resolved = await store.resolve_company(conn, posting.company_raw)
    if resolved is not None:
        return resolved
    if feed.source_type in {"ATS", "Direct"}:
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
) -> Counts:
    """Parse and project raw items. Parser exceptions intentionally propagate."""
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
            company_id = await _company_id(conn, feed, posting)
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
            _, changed, created = await store.upsert_job_posting(
                conn,
                source=source,
                source_key=posting.external_id,
                company_id=company_id,
                values=values,
                content_hash=_content_hash(values),
                raw_id=raw.raw_id,
                seen_at=seen_at,
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
) -> Counts:
    """Reparse each source key's latest bronze revision without scheduling."""
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
            conn, feed, [stored], envelope, seen_at=seen_at, dry_run=dry_run
        )
    return total
