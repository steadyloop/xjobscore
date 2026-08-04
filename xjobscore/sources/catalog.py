"""Source-owned, human-readable catalog of validated fetchable feeds.

The catalog deliberately owns endpoint instances while adapters own transport
and parsing.  This keeps an inspector independent of the application's
PostgreSQL ``feed`` table: a live catalog entry can always become a transient
``Feed`` and be harvested directly.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from xjobscore.records import Feed

__all__ = ["CatalogEntry", "CatalogError", "entries", "feed_ref", "feeds"]

CATALOG_DIR = Path(__file__).with_name("catalogs")
LIVE = "live"


class CatalogError(ValueError):
    """A source catalog is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    source: str
    key: str
    company_id: str
    company: str
    domain: str
    status: str
    enabled: bool
    validated_at: str | None
    config: dict


def _entry(source: str, raw: dict, path: Path) -> CatalogEntry:
    try:
        key = raw["key"]
        company_id = raw["company_id"]
        company = raw["company"]
        domain = raw["domain"]
        status = raw["status"]
    except KeyError as exc:
        raise CatalogError(f"{path}: feed entry is missing {exc.args[0]!r}") from None
    if not all(isinstance(value, str) and value for value in (key, company_id, company, domain, status)):
        raise CatalogError(
            f"{path}: key, company_id, company, domain, and status must be non-empty strings"
        )
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise CatalogError(f"{path}: enabled must be a boolean")
    validated_at = raw.get("validated_at")
    if validated_at is not None and not isinstance(validated_at, str):
        raise CatalogError(f"{path}: validated_at must be a string")
    config = raw.get("config", {})
    if not isinstance(config, dict):
        raise CatalogError(f"{path}: config must be a TOML table")
    return CatalogEntry(
        source=source,
        key=key,
        company_id=company_id,
        company=company,
        domain=domain,
        status=status,
        enabled=enabled,
        validated_at=validated_at,
        config=config,
    )


def entries() -> tuple[CatalogEntry, ...]:
    """Every declared catalog entry, sorted by source and exact feed key."""
    parsed: list[CatalogEntry] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(CATALOG_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            document = tomllib.load(handle)
        source = document.get("source")
        if not isinstance(source, str) or not source:
            raise CatalogError(f"{path}: source must be a non-empty string")
        if source != path.stem:
            raise CatalogError(f"{path}: source must match filename {path.stem!r}")
        rows = document.get("feed", [])
        if not isinstance(rows, list):
            raise CatalogError(f"{path}: feed must be an array of tables")
        for raw in rows:
            if not isinstance(raw, dict):
                raise CatalogError(f"{path}: each feed entry must be a table")
            entry = _entry(source, raw, path)
            identity = (entry.source, entry.key)
            if identity in seen:
                raise CatalogError(f"{path}: duplicate feed key {entry.key!r}")
            seen.add(identity)
            parsed.append(entry)
    return tuple(sorted(parsed, key=lambda entry: (entry.source, entry.key)))


def _feed_id(source: str, key: str) -> int:
    """Stable negative ID: catalog feeds never collide with database feed IDs."""
    digest = hashlib.sha256(f"{source}\0{key}".encode()).digest()
    value = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    return -(value or 1)


def feed_ref(source: str, key: str) -> str:
    """Stable string identity for an endpoint outside the Python process.

    Inspector APIs must not expose ``Feed.id`` as a JSON number: catalog IDs
    are 63-bit integers, while browser numbers are only exact through 53 bits.
    A full SHA-256 digest makes the public identity deterministic from the
    source/key pair without relying on JavaScript integer precision.
    """
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()


def feeds() -> tuple[Feed, ...]:
    """Live catalog entries materialized as transient, database-free feeds."""
    return tuple(
        Feed(
            id=_feed_id(entry.source, entry.key),
            source=entry.source,
            key=entry.key,
            config=entry.config,
            state={},
            company_id=entry.company_id,
            company_name=entry.company,
        )
        for entry in entries()
        if entry.status == LIVE and entry.enabled
    )
