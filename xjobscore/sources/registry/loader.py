"""Load and validate the source registry."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from xjobscore.records import Feed

__all__ = [
    "Company", "Entry", "RegistryError", "companies", "entries", "feed_ref", "feeds",
    "source_types",
]

REGISTRY_DIR = Path(__file__).parent
_SOURCE_TABLES = {"ats": "ATS", "direct": "Direct", "board": "Board"}
_FEED_FIELDS = {
    "key", "company_id", "company_name", "domain", "verified", "verified_at", "enabled", "config"
}
_SOURCE_FIELDS = {
    "ats": {"name", "catalog"},
    "direct": {"name", *_FEED_FIELDS},
    "board": {"name", *_FEED_FIELDS},
}


class RegistryError(ValueError):
    """Registry TOML is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Company:
    id: int
    name: str
    aliases: tuple[str, ...]
    domain: str
    industry: str
    desc: str
    verified: bool
    domain_verified_at: str | None


@dataclass(frozen=True, slots=True)
class Entry:
    source: str
    key: str  # ATS tenant token for ATS feeds; source-local key otherwise
    company_id: int
    company_name: str
    domain: str
    verified: bool
    verified_at: str
    enabled: bool
    config: dict


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"{path}: document must be a TOML table")
    return value


def _text(row: dict, name: str, path: Path) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{path}: {name} must be a non-empty string")
    return value


def _optional_text(row: dict, name: str, path: Path) -> str | None:
    value = row.get(name)
    if value is not None and (not isinstance(value, str) or not value):
        raise RegistryError(f"{path}: {name} must be a non-empty string when present")
    return value


def _positive_integer(row: dict, name: str, path: Path) -> int:
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RegistryError(f"{path}: {name} must be a positive integer")
    return value


def companies(registry_dir: Path = REGISTRY_DIR) -> tuple[Company, ...]:
    """Return validated canonical companies and aliases."""
    path = registry_dir.resolve() / "companies.toml"
    document = _read(path)
    if document.get("schema_version") != 2 or set(document) - {"schema_version", "company"}:
        raise RegistryError(f"{path}: invalid companies document")
    rows = document.get("company")
    if not isinstance(rows, list):
        raise RegistryError(f"{path}: company must be an array of tables")

    result: list[Company] = []
    ids: set[int] = set()
    alias_norms: set[str] = set()
    domains: set[str] = set()
    fields = {
        "id", "name", "aliases", "domain", "industry", "desc", "verified",
        "domain_verified_at",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) - fields:
            raise RegistryError(f"{path}: invalid company entry")
        company_id = _positive_integer(row, "id", path)
        if company_id in ids:
            raise RegistryError(f"{path}: invalid or duplicate company id {company_id!r}")
        domain = _text(row, "domain", path)
        verified = row.get("verified")
        if not isinstance(verified, bool):
            raise RegistryError(f"{path}: {company_id} verified must be a boolean")
        desc = row.get("desc")
        if not isinstance(desc, str):
            raise RegistryError(f"{path}: {company_id} desc must be a string")
        verified_at = _optional_text(row, "domain_verified_at", path)
        if verified_at is not None and not verified:
            raise RegistryError(f"{path}: {company_id} has a verification date but is not verified")
        if verified and domain == "unknown":
            raise RegistryError(f"{path}: {company_id} cannot verify an unknown domain")
        if domain != "unknown" and domain.lower() in domains:
            raise RegistryError(f"{path}: duplicate company domain {domain!r}")
        aliases = row.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            raise RegistryError(f"{path}: {company_id} aliases must be a non-empty array")
        for alias_norm in aliases:
            if not isinstance(alias_norm, str) or not alias_norm:
                raise RegistryError(f"{path}: invalid alias for {company_id!r}")
            if alias_norm in alias_norms:
                raise RegistryError(f"{path}: duplicate alias_norm {alias_norm!r}")
            alias_norms.add(alias_norm)
        ids.add(company_id)
        if domain != "unknown":
            domains.add(domain.lower())
        result.append(
            Company(
                id=company_id,
                name=_text(row, "name", path),
                aliases=tuple(aliases),
                domain=domain,
                industry=_text(row, "industry", path),
                desc=desc,
                verified=verified,
                domain_verified_at=verified_at,
            )
        )
    return tuple(sorted(result, key=lambda company: company.id))


def _entry(source: str, row: object, path: Path) -> Entry | None:
    if not isinstance(row, dict):
        raise RegistryError(f"{path}: each feed entry must be a table")
    fields = {
        "key", "company_id", "company_name", "domain", "verified", "verified_at", "enabled", "config"
    }
    unknown = set(row) - fields
    if unknown:
        raise RegistryError(f"{path}: unknown feed field {sorted(unknown)[0]!r}")
    candidate_fields = fields - {"config"}
    if set(row) == candidate_fields:
        if (
            isinstance(row["key"], str) and row["key"]
            and row["enabled"] is False
            and row["verified"] is False
            and row["verified_at"] == ""
            and row["company_id"] == 0
            and row["company_name"] == ""
            and row["domain"] == ""
        ):
            return None
    required = fields - {"config"}
    missing = required - set(row)
    if missing:
        raise RegistryError(f"{path}: missing feed field {sorted(missing)[0]!r}")
    verified = row.get("verified")
    if not isinstance(verified, bool):
        raise RegistryError(f"{path}: verified must be a boolean")
    verified_at = row.get("verified_at")
    if not isinstance(verified_at, str):
        raise RegistryError(f"{path}: verified_at must be a string")
    enabled = row.get("enabled")
    if not isinstance(enabled, bool):
        raise RegistryError(f"{path}: enabled must be a boolean")
    config = row.get("config", {})
    if not isinstance(config, dict):
        raise RegistryError(f"{path}: config must be a TOML table")
    return Entry(
        source=source,
        key=_text(row, "key", path),
        company_id=_positive_integer(row, "company_id", path),
        company_name=_text(row, "company_name", path),
        domain=_text(row, "domain", path),
        verified=verified,
        verified_at=verified_at,
        enabled=enabled,
        config=config,
    )


def _source_rows(registry_dir: Path) -> tuple[Path, Path, list[tuple[str, dict]]]:
    registry_dir = registry_dir.resolve()
    feeds_path = registry_dir / "feeds.toml"
    document = _read(feeds_path)
    if document.get("schema_version") != 2:
        raise RegistryError(f"{feeds_path}: schema_version must be 2")
    if set(document) - {"schema_version", *_SOURCE_TABLES}:
        raise RegistryError(f"{feeds_path}: unknown top-level field")

    source_rows: list[tuple[str, dict]] = []
    source_ids: set[str] = set()
    for table, source_type in _SOURCE_TABLES.items():
        rows = document.get(table, [])
        if not isinstance(rows, list):
            raise RegistryError(f"{feeds_path}: {table} must be an array of tables")
        for row in rows:
            if not isinstance(row, dict):
                raise RegistryError(f"{feeds_path}: each {table} entry must be a table")
            unknown = set(row) - _SOURCE_FIELDS[table]
            if unknown:
                raise RegistryError(f"{feeds_path}: unknown {table} field {sorted(unknown)[0]!r}")
            source = _text(row, "name", feeds_path)
            if source in source_ids:
                raise RegistryError(f"{feeds_path}: duplicate source {source!r}")
            source_ids.add(source)
            source_rows.append((source_type, row))
    return registry_dir, feeds_path, source_rows


def source_types(registry_dir: Path = REGISTRY_DIR) -> dict[str, str]:
    """Return validated source taxonomy derived from registry table names."""
    _, _, rows = _source_rows(registry_dir)
    return {row["name"]: source_type for source_type, row in rows}


def entries(registry_dir: Path = REGISTRY_DIR) -> tuple[Entry, ...]:
    """Return every validated feed, sorted by source and key."""
    registry_dir, feeds_path, source_rows = _source_rows(registry_dir)
    company_ids = {company.id for company in companies(registry_dir)}
    result: list[Entry] = []
    feed_ids: set[tuple[str, str]] = set()
    referenced_catalogs: set[Path] = set()

    for source_type, row in source_rows:
        source = row["name"]
        if source_type in {"Direct", "Board"}:
            catalog, inline = None, {key: value for key, value in row.items() if key != "name"}
        else:
            catalog, inline = row.get("catalog"), row.get("feed")
        if catalog is not None and inline is not None:
            raise RegistryError(f"{feeds_path}: {source} cannot declare both catalog and feed")
        if catalog is not None:
            if not isinstance(catalog, str) or not catalog:
                raise RegistryError(f"{feeds_path}: {source} catalog must be a non-empty string")
            path = (registry_dir / catalog).resolve()
            if not path.is_relative_to(registry_dir):
                raise RegistryError(f"{feeds_path}: {source} catalog must stay inside registry")
            document = _read(path)
            if document.get("schema_version") != 1 or document.get("source") != source:
                raise RegistryError(f"{path}: schema_version/source does not match {source!r}")
            if set(document) - {"schema_version", "source", "feed"}:
                raise RegistryError(f"{path}: unknown top-level field")
            rows = document.get("feed", [])
            if not isinstance(rows, list):
                raise RegistryError(f"{path}: feed must be an array of tables")
            referenced_catalogs.add(path)
        elif inline is not None:
            path, rows = feeds_path, [inline]
        else:
            continue

        for feed_row in rows:
            entry = _entry(source, feed_row, path)
            if entry is None:
                continue
            if entry.company_id not in company_ids:
                raise RegistryError(f"{path}: unknown company_id {entry.company_id!r}")
            identity = (source, entry.key)
            if identity in feed_ids:
                raise RegistryError(f"{path}: duplicate feed key {entry.key!r}")
            feed_ids.add(identity)
            result.append(entry)

    catalog_dir = registry_dir / "ats-catalog"
    if catalog_dir.exists():
        orphaned = {path.resolve() for path in catalog_dir.glob("*.toml")} - referenced_catalogs
        if orphaned:
            raise RegistryError(f"{min(orphaned)}: catalog is not referenced by feeds.toml")
    return tuple(sorted(result, key=lambda entry: (entry.source, entry.key)))


def _feed_id(source: str, key: str) -> int:
    value = int.from_bytes(hashlib.sha256(f"{source}\0{key}".encode()).digest()[:8], "big")
    return -(value & ((1 << 63) - 1) or 1)


def feed_ref(source: str, key: str) -> str:
    """Return the stable public identity of a feed."""
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()


def feeds(registry_dir: Path = REGISTRY_DIR) -> tuple[Feed, ...]:
    """Materialize enabled live entries as transient feeds."""
    types = source_types(registry_dir)
    return tuple(
        Feed(
            id=_feed_id(entry.source, entry.key), source=entry.source, key=entry.key,
            config=entry.config, company_id=entry.company_id, company_name=entry.company_name,
            source_type=types.get(entry.source, "Other"),
        )
        for entry in entries(registry_dir)
        if entry.verified and entry.enabled
    )
