"""Inspector database and staging storage settings."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE files without adding a dotenv dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _load_local_config() -> None:
    """Load repository configuration, preserving explicit environment values."""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = (
        Path.cwd() / ".env",
        repo_root / ".env",
    )
    for candidate in candidates:
        _load_dotenv(candidate)


_load_local_config()


def dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("No database DSN. Set DATABASE_URL.")
    _validate_dsn(value)
    return value


def dsn_or_none() -> str | None:
    """The policy layer's DSN; None runs the inspector in degraded mode."""
    try:
        return dsn()
    except RuntimeError:
        return None


def _validate_dsn(value: str) -> None:
    """Fail before asyncpg emits an opaque socket/IDNA traceback."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(
            "Invalid PostgreSQL DSN. Use postgresql://user:password@host:5432/database."
        ) from exc

    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError(
            "Invalid PostgreSQL DSN scheme. Use postgresql://user:password@host:5432/database."
        )
    if hostname:
        labels = hostname.split(".")
        if any(not label or len(label.encode("idna")) > 63 for label in labels):
            raise RuntimeError(
                "Invalid PostgreSQL DSN hostname. Replace placeholders such as '...' "
                "with the real database host."
            )


def persistence_dsn() -> str:
    """DSN for the inspector's own result store.

    Defaults to the same database the policy layer reads, because the store
    lives in its own schema and cannot collide with the core pipeline's
    tables. Point ``XJOBSCORE_INSPECTOR_STORE_DSN`` elsewhere to keep
    inspector staging data out of the pipeline database entirely.
    """
    value = os.environ.get("XJOBSCORE_INSPECTOR_STORE_DSN")
    if not value:
        return dsn()
    _validate_dsn(value)
    return value


def persistence_schema() -> str:
    """PostgreSQL schema owning the inspector's staging and history tables.

    Never ``public``: the inspector staging tables must not collide with the
    core pipeline's tables.
    """
    name = os.environ.get("XJOBSCORE_INSPECTOR_SCHEMA", "inspector")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise RuntimeError(
            "XJOBSCORE_INSPECTOR_SCHEMA must be a lowercase unquoted identifier."
        )
    if name == "public":
        raise RuntimeError(
            "XJOBSCORE_INSPECTOR_SCHEMA must not be 'public': the inspector's "
            "company/alias mirror would overwrite the core pipeline's tables."
        )
    return name
