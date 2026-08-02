"""Where a harvested `Record` lands. Bronze only -- `Record.items` become
`raw_document` rows; turning a raw item into `Posting`/`Target`/`Label`/`Fact`
is a parsing layer that does not exist yet (see ARCHITECTURE.md's Router
section). This module's whole job is the one arrow that *is* settled:
`Record` -> bronze.

It owns two decisions no other module should duplicate:

  the volatile-key table   a rotating field (Workday's "Posted Today",
                            Rippling's signed logo URL) must not make an
                            unchanged item look changed on every poll --
                            router hashes, so router owns the table.
  new vs. changed           `store.save_raw`'s bool only knows "this hash
                            already existed"; it cannot say whether the
                            external_id itself is new to the feed. That
                            split is what makes items_changed / items_listed
                            in poll_run a churn signal instead of noise.

`record.envelope` -- response-root facts no single item carries (Workable's
company name) -- rides along on every item row of the poll rather than a row
of its own: one fact, duplicated, needs no synthetic identity the way a
stand-alone row would.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from xjobscore import store
from xjobscore.records import Feed, Item, Record

if TYPE_CHECKING:  # asyncpg is store's implementation detail, not router's
    from asyncpg import Connection

__all__ = ["VOLATILE", "Counts", "route"]

# Key names stripped at any depth before hashing, never by path -- the names
# below are distinctive enough (opensearch, SearchId, postedOn) that matching
# by name alone is not a real collision risk, and a nested rename would
# silently reopen the leak a path-based rule would have caught by accident.
# Seeded from schema.sql's raw_document comment plus the fuller Oracle facet
# list in notebooks/ATS-payloads-alignment.md. A source missing here hashes
# its whole payload -- that is correct until someone observes it flapping.
VOLATILE: dict[str, frozenset[str]] = {
    "workday": frozenset({"postedOn"}),  # "Posted Today", rolls daily
    "rippling": frozenset({"logo"}),  # board.logo.url is signed, rotates per request
    "icims": frozenset({"opensearch", "last_mod"}),
    "oracle": frozenset({"Relevancy", "SearchId", "Facets"}),
}


@dataclass(frozen=True, slots=True)
class Counts:
    """What one `route` call did to raw_document."""

    new: int = 0
    changed: int = 0


def _strip(value: Any, volatile: frozenset[str]) -> Any:
    """Drop volatile keys at every depth, then sort what remains.

    Builds a new structure top to bottom; never touches the value it was
    handed. That is what lets the caller hash a throwaway copy and still hand
    `save_raw` the original, unmutated.
    """
    if isinstance(value, dict):
        return {k: _strip(v, volatile) for k, v in sorted(value.items()) if k not in volatile}
    if isinstance(value, list):
        return [_strip(v, volatile) for v in value]
    return value


def _content_hash(payload: dict | str, kind: str, volatile: frozenset[str]) -> str:
    """Hash of one item, stable across reserialization and volatile-key drift.

    `kind == 'json'` canonicalizes -- volatile keys dropped at any depth,
    remaining keys sorted -- before hashing, so a source that reorders its
    JSON or rolls a "Posted Today" does not read as a change. Any other kind
    (xml/html/mime) has no keys to strip, so it hashes its own utf-8 bytes
    as-is. Either way, only the hash sees a transformed copy -- `save_raw`
    always gets `payload` verbatim.
    """
    if kind == "json":
        stripped = _strip(payload, volatile)
        encoded = json.dumps(
            stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    else:
        encoded = payload.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _save_item(
    conn: Connection,
    feed_id: int,
    run_id: int,
    item: Item,
    volatile: frozenset[str],
    envelope: dict | None,
) -> bool:
    # kind='json' -> payload_json, everything else -> payload_text; the CHECK
    # on raw_document requires exactly one. connect() installs the jsonb
    # codec, so a plain dict passes through with no json.dumps here.
    payload_json = item.payload if item.kind == "json" else None
    payload_text = item.payload if item.kind != "json" else None
    return await store.save_raw(
        conn,
        feed_id,
        run_id,
        external_id=item.external_id,
        kind=item.kind,
        content_hash=_content_hash(item.payload, item.kind, volatile),
        payload_json=payload_json,
        payload_text=payload_text,
        envelope=envelope,
    )


async def route(conn: Connection, feed: Feed, record: Record, run_id: int) -> Counts:
    """Land one Record's items in raw_document. Bronze only.

    `new`/`changed` are computed against `store.seen_external_ids`, fetched
    once up front: an id absent from that set is growth, present with a
    stored row (`save_raw` returned True) is turnover. Both are refreshed as
    items are processed so two occurrences of the same external_id within one
    Record -- not expected, but not this function's job to forbid -- can't
    double-count as new.

    A 304 hands back an empty `Record`, so this is a no-op with no special
    case: the loop below simply does not run.
    """
    volatile = VOLATILE.get(feed.source, frozenset())
    envelope = record.envelope or None
    seen = await store.seen_external_ids(conn, feed.id)

    new = 0
    changed = 0
    for item in record.items:
        stored = await _save_item(conn, feed.id, run_id, item, volatile, envelope)
        if stored:
            if item.external_id in seen:
                changed += 1
            else:
                new += 1
        seen.add(item.external_id)

    return Counts(new=new, changed=changed)
