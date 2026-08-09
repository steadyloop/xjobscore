"""Configuration for the standalone inspector process."""

from __future__ import annotations

import json
import os
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
    """Honor the repository's existing local DB config when present.

    Explicit environment variables still win. This keeps the inspector
    independent of the viewer implementation while making it work out of the
    box in this repository, whose local development DSN is commonly stored in
    ``viewer/.env``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    candidates = (
        Path.cwd() / ".env",
        repo_root / ".env",
        repo_root / "viewer" / ".env",
        repo_root / "postings" / ".env",
    )
    for candidate in candidates:
        _load_dotenv(candidate)


_load_local_config()


def dsn() -> str:
    value = (
        os.environ.get("XJOBSCORE_INSPECTOR_DSN")
        or os.environ.get("XJOBSCORE_DSN")
        or os.environ.get("DATABASE_URL")
    )
    if not value:
        raise RuntimeError(
            "No database DSN. Set XJOBSCORE_INSPECTOR_DSN, XJOBSCORE_DSN, or DATABASE_URL."
        )
    _validate_dsn(value)
    return value


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


def user_agent() -> str:
    return os.environ.get("XJOBSCORE_INSPECTOR_USER_AGENT", "xjobscore-inspector/0.1")


def source_config(source: str) -> dict:
    """Optional local JSON config for a non-ATS source."""
    name = f"XJOBSCORE_INSPECTOR_{source.upper()}_CONFIG"
    value = os.environ.get(name)
    if not value:
        return {}
    try:
        config = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must contain a JSON object") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"{name} must contain a JSON object")
    return config


def persistence_path() -> Path:
    """SQLite path for inspector results; override for a project-local file."""
    value = os.environ.get("XJOBSCORE_INSPECTOR_DB")
    return Path(value).expanduser() if value else Path.home() / ".xjobscore-inspector.sqlite3"


def port() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_PORT", "8765"))
