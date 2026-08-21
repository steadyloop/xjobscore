"""Configuration for the standalone inspector process."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from xjobscore.net import DEFAULT_USER_AGENT


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


def user_agent() -> str:
    return os.environ.get("XJOBSCORE_INSPECTOR_USER_AGENT", DEFAULT_USER_AGENT)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def runner_enabled() -> bool:
    """Whether this process may run run.py's poll loop at all.

    Off by default: the scheduler_control DB flag is the operator's runtime
    pause, but this env var is the deploy-time decision that most inspector
    processes -- every dev laptop, the whole test suite -- should never poll
    production feeds no matter what that flag says.
    """
    return _env_bool("XJOBSCORE_INSPECTOR_RUNNER", False)


def runner_tick_s() -> float:
    return float(os.environ.get("XJOBSCORE_INSPECTOR_RUNNER_TICK_S", "60"))


def runner_batch() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_RUNNER_BATCH", "20"))


def runner_lease_s() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_RUNNER_LEASE_S", "3600"))


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

    Never ``public``: the inspector mirrors the registry into its own
    ``company``/``alias`` tables, which would otherwise overwrite the core
    pipeline's canonical rows of the same name.
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


def port() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_PORT", "8765"))
