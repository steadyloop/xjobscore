"""Runtime configuration for the PostgreSQL-backed localhost application."""

from dataclasses import dataclass, field
from pathlib import Path
import os


def _repo_root() -> Path:
    # Both defaults below live at the repo root, so the walk is written once:
    # getting the level count wrong resolves to a path that simply does not
    # exist, and the static-dir case fails silently (see app.py).
    return Path(__file__).resolve().parents[3]


def _default_static_dir() -> str:
    # The browser client lives outside this package at repo-root/frontend so it
    # can be deployed on its own. Serving it here keeps local development
    # same-origin; point this elsewhere, or at a missing path, to run API-only.
    return str(_repo_root() / "frontend")


def _emails(raw: str) -> frozenset[str]:
    # Comma-separated allowlist. Sign-in is invite-only: an address absent from
    # this set is refused at the OAuth callback, so an empty value locks everyone
    # out rather than letting anyone in.
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _flag(raw: str, *, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    dsn: str
    host: str = "127.0.0.1"
    port: int = 8766
    static_dir: str = field(default_factory=_default_static_dir)

    # Google sign-in. Empty values are allowed so tests can build an app without
    # credentials; /api/auth/login reports the misconfiguration instead.
    google_client_id: str = ""
    google_client_secret: str = ""
    session_secret: str = ""
    allowed_emails: frozenset[str] = frozenset()
    # Origin this server is reached at, used to build the OAuth redirect_uri. It
    # must match a URI registered in the Google console byte for byte.
    public_url: str = "http://127.0.0.1:8766"
    cookie_secure: bool = False


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
        static_dir=os.environ.get("XJOBSCORE_ONESHOT_STATIC_DIR") or _default_static_dir(),
        google_client_id=os.environ.get("XJOBSCORE_ONESHOT_GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.environ.get("XJOBSCORE_ONESHOT_GOOGLE_CLIENT_SECRET", ""),
        session_secret=os.environ.get("XJOBSCORE_ONESHOT_SESSION_SECRET", ""),
        allowed_emails=_emails(os.environ.get("XJOBSCORE_ONESHOT_ALLOWED_EMAILS", "")),
        public_url=(
            os.environ.get("XJOBSCORE_ONESHOT_PUBLIC_URL", "").rstrip("/")
            or "http://127.0.0.1:8766"
        ),
        cookie_secure=_flag(
            os.environ.get("XJOBSCORE_ONESHOT_COOKIE_SECURE", ""), default=False
        ),
    )
