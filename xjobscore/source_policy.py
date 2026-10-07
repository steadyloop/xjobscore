"""Vendors explicitly disabled by the operator, regardless of adapter support."""

DISABLED_SOURCES = frozenset({"paylocity"})


def source_disabled(source: str) -> bool:
    return source.strip().casefold() in DISABLED_SOURCES


def require_source_allowed(source: str) -> None:
    if source_disabled(source):
        raise ValueError(f"{source} is disabled and cannot be enabled or imported")


def visible_source_sql(column: str) -> str:
    """Internal SQL predicate; column names are fixed application identifiers."""
    if column not in {"ats", "t.ats", "l.ats", "source", "f.source"}:
        raise ValueError("unexpected source column")
    names = ", ".join("'" + name.replace("'", "''") + "'" for name in sorted(DISABLED_SOURCES))
    return f"lower({column}) NOT IN ({names})"
