"""Encode harvested records into schema.sql's replayable bronze contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from xjobscore import store
from xjobscore.records import Feed, Item, Record

if TYPE_CHECKING:
    from asyncpg import Connection

__all__ = [
    "VOLATILE",
    "PreparedRaw",
    "StoredRaw",
    "encoded_hash",
    "prepare",
    "persist",
    "prior_items",
    "snapshot_hash",
    "unwrap",
    "wrap",
]

VOLATILE: dict[str, frozenset[str]] = {
    # A skipped row stores the jobPostings[] row itself, so `postedOn` sits at
    # the payload root; a detail-merged row keeps it there too (`merge` puts the
    # list row underneath). `_strip` matches on key name at any depth, so one
    # entry covers both shapes.
    "workday": frozenset({"postedOn"}),
    "rippling": frozenset({"logo"}),
    "icims": frozenset({"opensearch", "last_mod"}),
    "oracle": frozenset({"Relevancy", "SearchId", "Facets"}),
}


@dataclass(frozen=True, slots=True)
class PreparedRaw:
    item: Item
    data: dict[str, Any]
    data_hash: str


@dataclass(frozen=True, slots=True)
class StoredRaw:
    item: Item
    raw_id: int
    data_hash: str
    created: bool
    data: dict[str, Any] | None = None


def _strip(value: Any, volatile: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {key: _strip(item, volatile) for key, item in sorted(value.items()) if key not in volatile}
    if isinstance(value, list):
        return [_strip(item, volatile) for item in value]
    return value


def encoded_hash(value: Any) -> str:
    """Stable digest of any JSON-encodable value, used wherever "same content
    again" must not depend on key order or Python's dict iteration."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Old private name: nothing outside this module calls it, kept only in case a
# caller (test, notebook, ad hoc script) still imports it by the old name.
_encoded_hash = encoded_hash


def _content_hash(payload: dict | str, kind: str, volatile: frozenset[str]) -> str:
    """Compatibility helper used by the inspector for a payload-only hash."""
    return encoded_hash({"kind": kind, "payload": _strip(payload, volatile)})


def prepare(feed: Feed, record: Record) -> list[PreparedRaw]:
    """Wrap every item uniformly while hashing a normalized copy."""
    volatile = VOLATILE.get(feed.source, frozenset())
    result = []
    for item in record.items:
        data = {
            "version": 1,
            "kind": item.kind,
            "payload": item.payload,
            "envelope": record.envelope,
        }
        normalized = {
            "version": 1,
            "kind": item.kind,
            "payload": _strip(item.payload, volatile),
            "envelope": _strip(record.envelope, volatile),
        }
        result.append(PreparedRaw(item=item, data=data, data_hash=encoded_hash(normalized)))
    return result


def wrap(payload: dict | str, *, kind: str = "json", envelope: dict | None = None) -> dict:
    """The versioned bronze wrapper for a payload that did not come from a feed.

    `prepare` handles the harvest path, where items arrive inside a `Record`.
    A probe produces exactly one payload and no record at all, so it needs the
    same wrapper without the surrounding machinery -- `unwrap` must be able to
    reconstruct it identically either way.
    """
    return {"version": 1, "kind": kind, "payload": payload, "envelope": envelope or {}}


def snapshot_hash(items: list[PreparedRaw]) -> str:
    """Hash item identity plus content independent of response ordering."""
    return encoded_hash(sorted((item.item.external_id, item.data_hash) for item in items))


def unwrap(data: Any) -> tuple[Item, dict]:
    """Reconstruct parser inputs from the versioned JSONB bronze wrapper."""
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("unsupported raw_posting.data wrapper")
    kind = data.get("kind")
    payload = data.get("payload")
    envelope = data.get("envelope") or {}
    if kind not in {"json", "xml", "html", "mime"}:
        raise ValueError("unsupported raw posting kind")
    if not isinstance(payload, (dict, str)) or not isinstance(envelope, dict):
        raise ValueError("malformed raw_posting.data wrapper")
    return Item(external_id="", payload=payload, kind=kind), envelope


async def prior_items(conn: Connection, source: str) -> dict[str, Item]:
    """The newest bronze payload per posting, keyed by source-local id.

    Feeds `json-api.py`'s revision-checked detail reuse (`sources.reusing`): a
    posting whose list row still carries the same revision fields as the
    payload found here skips its detail request entirely. `unwrap` reconstructs
    `external_id=""` by design (it only sees `raw_posting.data`, not the row),
    so it is re-attached here from `source_key` -- the same pattern
    `silver.replay` already uses. A wrapper `unwrap` rejects is treated as a
    miss, never a crash: this is a cache, and a bad row here must not cost the
    poll.
    """
    result: dict[str, Item] = {}
    for row in await store.latest_raw(conn, source):
        try:
            item, _ = unwrap(row["data"])
        except ValueError:
            continue
        result[row["source_key"]] = replace(item, external_id=row["source_key"])
    return result


async def persist(
    conn: Connection,
    feed: Feed,
    items: list[PreparedRaw],
) -> list[StoredRaw]:
    source = store.source_instance(feed)
    result = []
    for prepared in items:
        raw_id, created = await store.save_raw(
            conn,
            source=source,
            source_key=prepared.item.external_id,
            data=prepared.data,
            data_hash=prepared.data_hash,
        )
        result.append(
            StoredRaw(
                item=prepared.item,
                raw_id=raw_id,
                data_hash=prepared.data_hash,
                created=created,
                data=prepared.data,
            )
        )
    return result
