"""Configuration for the standalone inspector process."""

from __future__ import annotations

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


def token_heartbeat_enabled() -> bool:
    """Whether this process may run the token validation/promotion heartbeat.

    On by default, unlike runner_enabled(): one leased batch of liveness probes
    every six hours is the behaviour every inspector should have, and the
    operator's runtime pause lives in ``token_heartbeat.paused`` where the
    Tokens page can reach it. Set the env var falsy for a process that must
    never probe -- ``inspector/conftest.py`` pins it off for the test suite.
    """
    return _env_bool("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT", True)


def token_heartbeat_tick_s() -> float:
    return float(os.environ.get("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_TICK_S", "60"))


def token_heartbeat_batch() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_BATCH", "20"))


def token_heartbeat_interval_s() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_INTERVAL_S", "21600"))


def token_heartbeat_retry_s() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_RETRY_S", "900"))


def token_heartbeat_lease_s() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT_LEASE_S", "900"))


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


def port() -> int:
    return int(os.environ.get("XJOBSCORE_INSPECTOR_PORT", "8765"))
