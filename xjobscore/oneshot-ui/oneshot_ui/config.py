"""Runtime configuration for the self-contained localhost application."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8766
    user_agent: str = "xjobscore-oneshot/0.1"
    fetch_concurrency: int = 4


def load_settings() -> Settings:
    """Resolve environment overrides without reading or writing a .env file."""
    return Settings(
        database_path=Path(
            os.environ.get(
                "XJOBSCORE_ONESHOT_DB",
                str(APP_ROOT / "var" / "oneshot.sqlite3"),
            )
        ).expanduser(),
        host=os.environ.get("XJOBSCORE_ONESHOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("XJOBSCORE_ONESHOT_PORT", "8766")),
        user_agent=os.environ.get(
            "XJOBSCORE_ONESHOT_USER_AGENT", "xjobscore-oneshot/0.1"
        ),
        fetch_concurrency=max(
            1, min(12, int(os.environ.get("XJOBSCORE_ONESHOT_FETCH_CONCURRENCY", "4")))
        ),
    )

