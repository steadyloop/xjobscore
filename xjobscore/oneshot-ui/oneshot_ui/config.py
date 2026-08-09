"""Runtime configuration for the PostgreSQL-backed localhost application."""

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Settings:
    dsn: str
    host: str = "127.0.0.1"
    port: int = 8766


def load_settings() -> Settings:
    return Settings(
        dsn=(
            os.environ.get("XJOBSCORE_ONESHOT_DSN")
            or os.environ.get("XJOBSCORE_DSN")
            or os.environ.get("DATABASE_URL")
            or ""
        ),
        host=os.environ.get("XJOBSCORE_ONESHOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("XJOBSCORE_ONESHOT_PORT", "8766")),
    )
