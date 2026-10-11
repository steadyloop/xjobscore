"""Parse replayable bronze rows into schema.sql's ``job_posting`` projection."""

from __future__ import annotations

from html.parser import HTMLParser
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from xjobscore import probe, router, sources, store
from xjobscore.location_normalizer import normalize_location
from xjobscore.records import LINK_DEAD, PAGE_CLOSED, Feed, Item, Posting, Unparseable

if TYPE_CHECKING:
    from asyncpg import Connection

logger = logging.getLogger(__name__)

__all__ = ["ChangeSet", "Counts", "process", "replay"]


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
        "part_time": posting.part_time,
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
#                 reads either (skills_input_hash hashes `description` alone,
#                 and the degree step stamps a column), and the upsert merges
#                 attributes in both CASE branches regardless of content_hash,
#                 so excluding it here costs no data -- only a churn signal
#                 that was never wanted.
#   job_type, remote, part_time, locations -- parse-time derivations. Parser
#                 improvements
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
    # and only this counter may reach ``poll_log.new_postings``.
    created: int = 0
    unchanged: int = 0
    unresolved: int = 0
    unsupported: int = 0
    secondhand: int = 0
    # Items an adapter refused as not-a-posting. Counted, never silent: this is
    # the only channel that tells an operator a feed's detail pages have gone
    # from requisitions to session walls.
    unparseable: int = 0
    # Companies created for a token that had none (see ``_mint_for_feed``).
    # A token linked to an existing company is not counted.
    minted: int = 0
    # Identity side of the result used by the unified incremental Gold path.
    # ``compare=False`` preserves the value semantics of historical counter
    # assertions while making the changed rows explicit to new callers.
    changed_posting_ids: frozenset[int] = field(
        default_factory=frozenset, compare=False
    )
    affected_company_ids: frozenset[int] = field(
        default_factory=frozenset, compare=False
    )

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            postings=self.postings + other.postings,
            created=self.created + other.created,
            unchanged=self.unchanged + other.unchanged,
            unresolved=self.unresolved + other.unresolved,
            unsupported=self.unsupported + other.unsupported,
            secondhand=self.secondhand + other.secondhand,
            unparseable=self.unparseable + other.unparseable,
            minted=self.minted + other.minted,
            changed_posting_ids=self.changed_posting_ids
            | other.changed_posting_ids,
            affected_company_ids=self.affected_company_ids
            | other.affected_company_ids,
        )


@dataclass(slots=True)
class ChangeSet:
    """Silver rows whose derived Gold state may need to be refreshed.

    The mutable collector is optional so replay and older callers retain the
    compact Counts return value. The unified runtime passes one per feed and
    carries only these identities into the incremental Gold path.
    """

    posting_ids: set[int]
    company_ids: set[int]

    def __init__(self) -> None:
        self.posting_ids = set()
        self.company_ids = set()

    def add(self, posting_id: int, company_id: int) -> None:
        self.posting_ids.add(posting_id)
        self.company_ids.add(company_id)


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


async def _mint_for_feed(
    conn: Connection, feed: Feed, posting: Posting
) -> tuple[int, bool]:
    """Give a company-less ATS token its company, from this posting's name.

    An ATS feed's token *is* the employer, so a posting that resolves to
    nothing names a company we have not met yet, not one to park. The name is
    the posting's ``company_raw`` (Greenhouse and Comeet carry a real one;
    Ashby and Lever map the slug) and the slug otherwise. The row lands
    ``pending`` for the Companies page to confirm. Returns
    ``(company_id, minted)``; an existing company found by name or slug is
    linked, not duplicated.
    """
    company_id, minted = await store.ensure_token_company(
        conn, ats=feed.source, token=feed.key, name=posting.company_raw
    )
    logger.info(
        "%s company source=%s company_id=%d name=%r",
        "minted" if minted else "linked",
        store.source_instance(feed),
        company_id,
        posting.company_raw,
    )
    return company_id, minted


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
    whole_batch: bool = True,
    changes: ChangeSet | None = None,
) -> Counts:
    """Parse and project raw items. Parser exceptions intentionally propagate.

    The one exception is ``Unparseable``, which an adapter raises only for a
    payload it can positively identify as not-a-posting. That item is counted
    and skipped so one dead detail page cannot cost the batch every other
    posting beside it. If *every* item is unparseable the batch still fails:
    that is not one closed requisition, it is a feed whose detail pages stopped
    being detail pages, and it must reach the scheduler as an error.

    ``touch_seen`` is false when the caller did not observe the live listing --
    a replay over stored payloads, or a probe of a single posting. Only a fetch
    that actually returned the posting may advance ``last_seen_at``.

    ``whole_batch`` is false when ``stored`` is a slice of the caller's batch
    rather than the batch itself. The all-unparseable check only means anything
    against the full set, so a caller feeding items in chunks must run it once
    over its own total instead -- see ``replay``.
    """
    parse = sources.parser(feed.source)
    total = Counts()
    source = store.source_instance(feed)
    refused: Unparseable | None = None
    simplify_tokens: list[tuple[str, str, int]] = []
    # Mint only on the live poll path. ``dry_run`` writes nothing, and a caller
    # passing an alias snapshot declared that nothing writes aliases mid-pass.
    can_mint = feed.type == "ATS" and not dry_run and aliases is None
    minted_id: int | None = None
    # One company fill per batch: every posting of a board says the same thing.
    filled: set[int] = set()
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
        try:
            if isinstance(item.payload, dict) and "_probe" in item.payload:
                canonicals = probe.parse_tombstone(item, item.payload["_probe"])
            else:
                canonicals = parse(feed, item, item_envelope)
        except Unparseable as error:
            refused = error
            logger.warning(
                "unparseable item source=%s source_key=%s: %s",
                source,
                raw.item.external_id,
                error,
            )
            total += Counts(unparseable=1)
            continue
        postings = []
        for canonical in canonicals:
            if not isinstance(canonical, Posting):
                total += Counts(unsupported=1)
            elif canonical.fidelity != "origin" and not (
                feed.source == "simplify" and canonical.fidelity == "secondhand"
            ):
                total += Counts(secondhand=1)
            else:
                postings.append(canonical)
        for posting in postings:
            # A probed dead link annotates a row that already exists; it never
            # creates one and carries nothing to project. Routing it through the
            # canonical upsert would overwrite title, description, and URLs with
            # the empty fields a tombstone necessarily has.
            if posting.closed_evidence in {LINK_DEAD, PAGE_CLOSED}:
                if dry_run:
                    total += Counts(postings=1)
                    continue
                closure_at = seen_at
                evidence = item.payload.get("_probe", {}) if isinstance(item.payload, dict) else {}
                if evidence.get("probed_at"):
                    observed = datetime.fromisoformat(evidence["probed_at"].replace("Z", "+00:00"))
                    if observed.tzinfo is not None:
                        closure_at = observed
                closed = await store.close_posting_by_key(
                    conn,
                    source=source,
                    source_key=posting.external_id,
                    closed_at=closure_at,
                    **({"closure_kind": "page"} if posting.closed_evidence == PAGE_CLOSED else {}),
                )
                total += Counts(postings=1) if closed else Counts(unchanged=1)
                continue
            is_simplify = feed.source == "simplify" and posting.fidelity == "secondhand"
            if is_simplify:
                from xjobscore import simplify_postings

                posting = simplify_postings.project(posting, item.payload)
                company_id, minted = await simplify_postings.company(
                    conn, posting, dry_run=dry_run, aliases=aliases,
                )
                total += Counts(minted=int(minted))
            else:
                company_id = await _company_id(conn, feed, posting, aliases=aliases)
            if company_id is None:
                # Once minted, the rest of the batch falls back to it exactly
                # as it would to the token's company on the next poll.
                company_id = minted_id
            if company_id is None and can_mint and not is_simplify:
                company_id, minted = await _mint_for_feed(conn, feed, posting)
                minted_id = company_id
                total += Counts(minted=int(minted))
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
            if is_simplify:
                values["attributes"].update(simplify_postings.metadata(item.payload))
            if dry_run:
                total += Counts(postings=1)
                continue
            if (
                not is_simplify
                and company_id not in filled
                and (posting.company_domain or posting.company_description)
            ):
                filled.add(company_id)
                await store.fill_company(
                    conn,
                    company_id,
                    domain=posting.company_domain,
                    description=posting.company_description,
                )
            # ats_closed_at is the stated channel only. No link_checked_at
            # here: a stated closure arrives with the listing, and that moment
            # is already last_seen_at -- link_checked_at belongs to the probe
            # channel alone, a fetch of the posting's own URL.
            closed = values["status"] == "closed"
            posting_id, changed, created = await store.upsert_job_posting(
                conn,
                source=source,
                source_key=posting.external_id,
                company_id=company_id,
                values=values,
                content_hash=_content_hash(values),
                raw_id=raw.raw_id,
                seen_at=seen_at,
                touch_seen=touch_seen,
                ats_closed_at=seen_at if closed else None,
            )
            if is_simplify:
                candidate = simplify_postings.token_candidate(posting.url_posting, company_id)
                if candidate is not None:
                    simplify_tokens.append(candidate)
            if changed and changes is not None:
                changes.add(posting_id, company_id)
            total += (
                Counts(
                    postings=1,
                    created=1 if created else 0,
                    changed_posting_ids=frozenset((posting_id,)),
                    affected_company_ids=frozenset((company_id,)),
                )
                if changed
                else Counts(unchanged=1)
            )
    if whole_batch and refused is not None and total.unparseable == len(stored):
        # Nothing survived. A per-item skip is a vendor answering about one
        # posting; a whole batch of them is the adapter reading a page it no
        # longer recognises, and reporting "ok, zero postings" for that would
        # hide a broken feed behind a healthy schedule.
        raise ValueError(
            f"every item was unparseable source={source} items={len(stored)}"
        ) from refused
    if simplify_tokens:
        await simplify_postings.discover(conn, simplify_tokens, seen_at)
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
    ATS closure timestamps use the raw observation's ``fetched_at``.
    """
    total = Counts()
    sources.parser(feed.source)  # fail before writing any replayed rows
    source = store.source_instance(feed)
    rows = await store.latest_raw(conn, source)
    for row in rows:
        item, envelope = router.unwrap(row["data"])
        item = Item(external_id=row["source_key"], payload=item.payload, kind=item.kind)
        stored = router.StoredRaw(
            item=item,
            raw_id=row["id"],
            data_hash=row["data_hash"],
            created=False,
            data=row["data"],
        )
        # One call per row, because each revision carries its own envelope. That
        # makes every call a one-item batch, so `process` cannot run the
        # all-unparseable check for us without failing the feed on the first
        # skippable page; the same check runs once below, over the whole feed.
        total += await process(
            conn, feed, [stored], envelope, seen_at=row["fetched_at"], dry_run=dry_run,
            aliases=aliases, touch_seen=False, whole_batch=False,
        )
    if rows and total.unparseable == len(rows):
        raise ValueError(
            f"every item was unparseable source={source} items={len(rows)}"
        )
    return total
