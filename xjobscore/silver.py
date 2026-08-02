"""Bronze -> silver: the replay pass. Turns stored `raw_document` rows into
`posting` rows (and, when a parser emits them, `feed` rows from `Target`).

This is deliberately not called from `run.poll`. `harvest` -> `router.route`
lands bronze on the poll's own schedule; `replay` reads what bronze already
holds, on a schedule of its own. That split is what makes "replayable" -- the
word `schema.sql` uses for `raw_document` -- actually true: a parsing bug is
fixed by running `replay` again, never by re-fetching a posting that may have
closed since.

`replay` reads the *latest* revision per `external_id` (`store.latest_raw`,
served by `raw_document_lookup`) rather than tracking a watermark. There is no
cursor in `feed.state` for this pass and no new DDL -- re-running is a no-op
because `store.upsert_posting` compares `content_hash` and, when it matches,
touches only `last_seen_at`.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from xjobscore import sources, store
from xjobscore.records import Feed, Item, Posting, Target

if TYPE_CHECKING:  # asyncpg is store's implementation detail, not silver's
    from asyncpg import Connection

__all__ = ["Counts", "normalize_title", "replay"]

# Jane Street serves Lisu code points as Latin look-alikes in job titles
# (schema.sql's own comment on posting.title_normalized): U+A4DF/A4E1/A4E3 for
# M/L/R. This is a seed, not the full Unicode TR39 confusables table -- add to
# it only against an observed title, the same rule VOLATILE in router.py
# follows for hash-stripped keys.
_CONFUSABLES = str.maketrans({
    "ꓟ": "M",  # Lisu LETTER MA
    "ꓡ": "L",  # Lisu LETTER LA
    "ꓣ": "R",  # Lisu LETTER RA
})


def normalize_title(t: str) -> str:
    """The one function that writes `posting.title_normalized`.

    Must stay byte-identical to `normalize_title()` in seeds/gen_umbrellas.py
    below the confusables fold -- NFKC, casefold, collapse to [a-z0-9] -- or
    role matching silently misses. The confusables `translate` is a no-op on
    plain ASCII titles, so the two functions agree everywhere gen_umbrellas.py
    has been exercised; it only diverges on the Jane Street titles that motivated
    this column being engine-written in the first place.
    """
    t = t.translate(_CONFUSABLES)
    t = unicodedata.normalize("NFKC", t).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _posting_hash(posting: Posting) -> str:
    """Hash of what we actually store, distinct from router.py's bronze hash.

    router's `_content_hash` hashes the vendor payload verbatim (minus
    volatile keys) so an unchanged poll inserts nothing new. This hashes the
    parsed `Posting`, so `store.upsert_posting` can tell an unchanged parse
    from a changed one and touch only `last_seen_at` on the former --
    unrelated questions that happen to share a hashing shape.
    """
    encoded = json.dumps(asdict(posting), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Counts:
    """What one `replay` call did."""

    postings: int = 0  # inserted or changed
    unchanged: int = 0  # parsed, hash matched, only last_seen touched
    unresolved: int = 0  # company_raw had no company_alias hit; parked in company_new
    targets: int = 0  # Target -> feed rows
    dropped: int = 0  # Label / Fact -- no destination table yet (store.put_label/put_fact)


async def replay(conn: Connection, feed: Feed, *, dry_run: bool = False) -> Counts:
    """Parse every current `raw_document` row of one feed into silver.

    `dry_run=True` parses and resolves companies (a read) but performs no
    write -- parity with `run.cycle`'s own dry-run, which harvests and routes
    but writes nothing.
    """
    parse = sources.parser(feed.source)
    rows = await store.latest_raw(conn, feed.id)

    postings = unchanged = unresolved = targets = dropped = 0

    for row in rows:
        item = Item(
            external_id=row["external_id"],
            payload=row["payload_json"] if row["kind"] == "json" else row["payload_text"],
            kind=row["kind"],
        )
        envelope = row["envelope"] or {}

        for canonical in parse(feed, item, envelope):
            if isinstance(canonical, Posting):
                company_id = None
                if canonical.company_raw:
                    company_id = await store.resolve_company(conn, canonical.company_raw)
                if company_id is None:
                    if canonical.company_raw and not dry_run:
                        await store.note_unknown_company(conn, canonical.company_raw, feed.source)
                    unresolved += 1
                    continue

                if dry_run:
                    postings += 1
                    continue

                # posting.last_seen_poll_run_id is stamped from the *bronze
                # row's own* poll_run_id, not a live run this pass is part
                # of -- replay has no poll_run of its own. This is known to
                # be wrong for closure-by-absence once reconcile.py exists;
                # see ARCHITECTURE.md's Open items.
                _, changed = await store.upsert_posting(
                    conn,
                    feed.id,
                    company_id,
                    canonical,
                    run_id=row["poll_run_id"],
                    title_normalized=normalize_title(canonical.title),
                    content_hash=_posting_hash(canonical),
                )
                if changed:
                    postings += 1
                else:
                    unchanged += 1

            elif isinstance(canonical, Target):
                if not dry_run:
                    await store.upsert_feed(conn, canonical.source, canonical.key, canonical.config)
                targets += 1

            else:  # Label | Fact -- store.put_label/put_fact still raise NotImplementedError
                dropped += 1

    return Counts(
        postings=postings,
        unchanged=unchanged,
        unresolved=unresolved,
        targets=targets,
        dropped=dropped,
    )
