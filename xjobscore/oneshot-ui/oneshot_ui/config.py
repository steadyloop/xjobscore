"""Runtime configuration for the self-contained localhost application."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8766
    user_agent: str = "xjobscore-oneshot/0.1"
    fetch_concurrency: int = 4
    # Demo mode swaps in an offline gateway and a separate database, and seeds
    # it on first start. It never touches the ordinary database.
    demo: bool = False


def load_settings(*, demo: bool | None = None) -> Settings:
    """Resolve environment overrides without reading or writing a .env file."""
    demo_mode = (
        demo
        if demo is not None
        else os.environ.get("XJOBSCORE_ONESHOT_DEMO", "").strip().casefold() in _TRUE
    )
    default_database = APP_ROOT / "var" / ("demo.sqlite3" if demo_mode else "oneshot.sqlite3")
    return Settings(
        database_path=Path(
            os.environ.get("XJOBSCORE_ONESHOT_DB", str(default_database))
        ).expanduser(),
        host=os.environ.get("XJOBSCORE_ONESHOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("XJOBSCORE_ONESHOT_PORT", "8766")),
        user_agent=os.environ.get(
            "XJOBSCORE_ONESHOT_USER_AGENT", "xjobscore-oneshot/0.1"
        ),
        fetch_concurrency=max(
            1, min(12, int(os.environ.get("XJOBSCORE_ONESHOT_FETCH_CONCURRENCY", "4")))
        ),
        demo=demo_mode,
    )

