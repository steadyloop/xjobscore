"""Load and validate the source registry."""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from xjobscore.records import Feed

__all__ = [
    "COMPANY_STAGES", "Company", "Entry", "RegistryError", "companies", "entries",
    "feed_ref", "feeds", "source_descriptions", "source_types",
]

REGISTRY_DIR = Path(__file__).parent / "registry"
_SOURCE_TABLES = {"ats": "ATS", "direct": "Direct", "board": "Board"}
# Every top-level document the loader reads.
_ROOT_DOCUMENTS = frozenset({"companies.toml", "feeds.toml"})
# ATS catalog rows. ``company_name``, ``domain``, and ``verified_at`` are still
# accepted so the 40 catalogs keep parsing, but they are ignored: company
# identity is derived from companies.toml, which is what makes the two copies
# unable to disagree.
_FEED_FIELDS = {
    "key", "company_id", "company_name", "domain", "verified", "verified_at", "config"
}
# Per-feed fields on an inline Direct or Board row. A Board resolves its company
# per posting rather than per feed (see ``silver._company_id``), so declaring one
# here would be inert -- the field is rejected instead of silently ignored.
_INLINE_FIELDS = {
    "Direct": {"key", "company_id", "verified", "config"},
    "Board": {"key", "verified", "config"},
}
_ENTRY_FIELDS = {"ATS": _FEED_FIELDS, **_INLINE_FIELDS}
_ENTRY_REQUIRED = {
    "ATS": {"key", "company_id", "verified"},
    "Direct": {"key", "company_id", "verified"},
    "Board": {"key", "verified"},
}
# Declared once per source, never per feed: ``description`` says what the source
# is, and ``implemented`` records whether an adapter for it actually works yet.
_SOURCE_ONLY_FIELDS = frozenset({"name", "description", "implemented"})
_SOURCE_FIELDS = {
    "ats": {*_SOURCE_ONLY_FIELDS, "catalog"},
    "direct": {*_SOURCE_ONLY_FIELDS, *_INLINE_FIELDS["Direct"]},
    "board": {*_SOURCE_ONLY_FIELDS, *_INLINE_FIELDS["Board"]},
}
# Company maturity. Optional in the TOML: an absent key means "unknown", so an
# unclassified company never asserts that it is not a startup.
COMPANY_STAGES = frozenset(
    {"startup", "growth", "public", "private", "nonprofit", "unknown"}
)


class RegistryError(ValueError):
    """Registry TOML is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Company:
    id: int
    name: str
    aliases: tuple[str, ...]
    domain: str
    industry: str
    stage: str
    desc: str
    verified: bool
    domain_verified_at: str | None


@dataclass(frozen=True, slots=True)
class Entry:
    source: str
    key: str  # ATS tenant token for ATS feeds; source-local key otherwise
    company_id: int | None  # None for Boards, which resolve company per posting
    company_name: str | None  # derived from companies.toml, never declared
    description: str  # what the source is; declared once, on the source
    implemented: bool  # whether a working adapter exists for the source
    verified: bool
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


def _company_rows(registry_dir: Path) -> tuple[tuple[Company, ...], frozenset[int]]:
    """Validate ``companies.toml`` and split it into active companies and excluded IDs.

    Excluded rows (``excluded = true``) are catalog-inventory archive: a company
    retired from the active registry so its feeds are intentionally inactive,
    without destructively deleting the feed rows that still point at it. They
    are validated exactly like active rows -- including alias and domain
    uniqueness against the whole file -- so retiring a company is a one-line
    edit, not a move to a second document.
    """
    path = registry_dir.resolve() / "companies.toml"
    document = _read(path)
    if document.get("schema_version") != 2 or set(document) - {"schema_version", "company"}:
        raise RegistryError(f"{path}: invalid companies document")
    rows = document.get("company")
    if not isinstance(rows, list):
        raise RegistryError(f"{path}: company must be an array of tables")

    result: list[Company] = []
    excluded_ids: set[int] = set()
    ids: set[int] = set()
    alias_norms: set[str] = set()
    domains: set[str] = set()
    fields = {
        "id", "name", "aliases", "domain", "industry", "stage", "desc", "verified",
        "domain_verified_at", "excluded",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) - fields:
            raise RegistryError(f"{path}: invalid company entry")
        company_id = _positive_integer(row, "id", path)
        if company_id in ids:
            raise RegistryError(f"{path}: invalid or duplicate company id {company_id!r}")
        name = _text(row, "name", path)
        industry = _text(row, "industry", path)
        domain = _text(row, "domain", path)
        verified = row.get("verified")
        if not isinstance(verified, bool):
            raise RegistryError(f"{path}: {company_id} verified must be a boolean")
        excluded = row.get("excluded", False)
        if not isinstance(excluded, bool):
            raise RegistryError(f"{path}: {company_id} excluded must be a boolean")
        desc = row.get("desc")
        if not isinstance(desc, str):
            raise RegistryError(f"{path}: {company_id} desc must be a string")
        stage = row.get("stage", "unknown")
        if stage not in COMPANY_STAGES:
            raise RegistryError(f"{path}: {company_id} stage must be one of {sorted(COMPANY_STAGES)}")
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
        if excluded:
            excluded_ids.add(company_id)
            continue
        result.append(
            Company(
                id=company_id,
                name=name,
                aliases=tuple(aliases),
                domain=domain,
                industry=industry,
                stage=stage,
                desc=desc,
                verified=verified,
                domain_verified_at=verified_at,
            )
        )
    active = tuple(sorted(result, key=lambda company: company.id))
    return active, frozenset(excluded_ids)


def companies(registry_dir: Path = REGISTRY_DIR) -> tuple[Company, ...]:
    """Return validated canonical companies and aliases, excluding archived ones."""
    active, _ = _company_rows(registry_dir)
    return active


def _entry(source: str, source_type: str, row: object, path: Path) -> Entry | None:
    """Validate one feed row.

    ``company_name``, ``description``, and ``implemented`` are resolved later by
    ``entries``: the first from companies.toml, the other two from the source.
    """
    if not isinstance(row, dict):
        raise RegistryError(f"{path}: each feed entry must be a table")
    unknown = set(row) - _ENTRY_FIELDS[source_type]
    if unknown:
        raise RegistryError(f"{path}: unknown feed field {sorted(unknown)[0]!r}")
    missing = _ENTRY_REQUIRED[source_type] - set(row)
    if missing:
        raise RegistryError(f"{path}: missing feed field {sorted(missing)[0]!r}")
    # An unresolved discovery candidate: a tenant token observed in the wild but
    # not yet tied to a canonical company. Ignored until someone resolves it.
    if row.get("company_id") == 0:
        return None
    verified = row.get("verified")
    if not isinstance(verified, bool):
        raise RegistryError(f"{path}: verified must be a boolean")
    config = row.get("config", {})
    if not isinstance(config, dict):
        raise RegistryError(f"{path}: config must be a TOML table")
    company_id = (
        _positive_integer(row, "company_id", path) if "company_id" in row else None
    )
    return Entry(
        source=source,
        key=_text(row, "key", path),
        company_id=company_id,
        company_name=None,
        description="",  # filled in by ``entries`` from the source declaration
        implemented=True,
        verified=verified,
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
            _text(row, "description", feeds_path)
            if not isinstance(row.get("implemented", True), bool):
                raise RegistryError(f"{feeds_path}: {source} implemented must be a boolean")
            source_ids.add(source)
            source_rows.append((source_type, row))
    return registry_dir, feeds_path, source_rows


def source_types(registry_dir: Path = REGISTRY_DIR) -> dict[str, str]:
    """Return validated source taxonomy derived from registry table names."""
    _, _, rows = _source_rows(registry_dir)
    return {row["name"]: source_type for source_type, row in rows}


def source_descriptions(registry_dir: Path = REGISTRY_DIR) -> dict[str, str]:
    """Return each source's description, including sources with no live feed."""
    _, _, rows = _source_rows(registry_dir)
    return {row["name"]: row["description"] for _, row in rows}


def entries(registry_dir: Path = REGISTRY_DIR) -> tuple[Entry, ...]:
    """Return every validated feed, sorted by source and key."""
    registry_dir, feeds_path, source_rows = _source_rows(registry_dir)
    active_companies, excluded_company_ids = _company_rows(registry_dir)
    company_names = {company.id: company.name for company in active_companies}
    result: list[Entry] = []
    feed_ids: set[tuple[str, str]] = set()
    referenced_catalogs: set[Path] = set()

    for source_type, row in source_rows:
        source = row["name"]
        description = row["description"]
        implemented = row.get("implemented", True)
        if source_type in {"Direct", "Board"}:
            inline = {
                key: value
                for key, value in row.items()
                if key not in _SOURCE_ONLY_FIELDS
            }
            # A single-feed source is its own endpoint, so the key repeats the
            # source name unless the adapter needs a different one -- simplify's
            # key is the GitHub owner/repo path its URL template interpolates.
            inline.setdefault("key", source)
            catalog = None
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
            entry = _entry(source, source_type, feed_row, path)
            if entry is None:
                continue
            if entry.company_id is not None:
                if entry.company_id in excluded_company_ids:
                    continue
                if entry.company_id not in company_names:
                    raise RegistryError(f"{path}: unknown company_id {entry.company_id!r}")
                entry = replace(entry, company_name=company_names[entry.company_id])
            entry = replace(entry, description=description, implemented=implemented)
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
    # A document nobody reads is indistinguishable from one whose edits silently
    # do nothing, so the registry root is closed the same way catalogs are.
    stray = {path for path in registry_dir.glob("*.toml") if path.name not in _ROOT_DOCUMENTS}
    if stray:
        raise RegistryError(f"{min(stray)}: unexpected registry document")
    return tuple(sorted(result, key=lambda entry: (entry.source, entry.key)))


def _feed_id(source: str, key: str) -> int:
    value = int.from_bytes(hashlib.sha256(f"{source}\0{key}".encode()).digest()[:8], "big")
    return -(value & ((1 << 63) - 1) or 1)


def feed_ref(source: str, key: str) -> str:
    """Return the stable public identity of a feed."""
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()


def feeds(registry_dir: Path = REGISTRY_DIR) -> tuple[Feed, ...]:
    """Materialize verified live entries as transient feeds.

    Whether a feed is polled is operator state owned by PostgreSQL's
    ``feed.enabled`` (set from the inspector); the registry only decides
    which feeds exist.
    """
    types = source_types(registry_dir)
    return tuple(
        Feed(
            id=_feed_id(entry.source, entry.key), source=entry.source, key=entry.key,
            config=entry.config, company_id=entry.company_id, company_name=entry.company_name,
            description=entry.description, implemented=entry.implemented,
            source_type=types.get(entry.source, "Other"),
        )
        for entry in entries(registry_dir)
        if entry.verified
    )
