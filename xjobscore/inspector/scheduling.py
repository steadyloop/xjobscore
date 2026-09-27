"""Pure operational derivation for the Scheduler page.

No I/O, no network, no wall-clock reads. Callers assemble the inputs and pass an
explicit ``now``, so everything here is unit-testable without a database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Missed cadences before a feed reads as stale.
STALE_INTERVAL_MULTIPLE = 3
# feed.strikes at which the engine's backoff is visibly in control.
BACKING_OFF_STRIKES = 3

STATE_LABELS = {
    "unknown": "—",
    "disabled": "Disabled",
    "paused": "Paused",
    "backing_off": "Backing off",
    "never_polled": "Never polled",
    "stale": "Stale",
    "healthy": "Healthy",
}

# Each state borrows one of the five .chip[data-status] colors components.css
# already defines, so this page adds no new status palette.
STATE_CHIPS = {
    "unknown": "never",
    "disabled": "never",
    "paused": "never",
    "backing_off": "partial",
    "never_polled": "never",
    "stale": "partial",
    "healthy": "ok",
}


def parse_iso(value: str | None) -> datetime | None:
    """Tolerant ISO parse; policy timestamps arrive as strings or not at all."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def humanize_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def requests_per_day(interval_s: int | None) -> float | None:
    """Configured request load for one active feed."""
    if not interval_s:
        return None
    return 86_400 / interval_s


def _status(state: str, detail: str) -> dict[str, Any]:
    return {
        "state": state,
        "label": STATE_LABELS[state],
        "chip": STATE_CHIPS[state],
        "detail": detail,
    }


def feed_status(
    policy: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """One pill folding together polling, strikes, and staleness.

    Ordered most-severe first and first-match-wins: a paused feed is not also
    reported as stale, because the operator only needs the reason that actually
    explains what they are looking at. Deliberate intent outranks every derived
    signal below it -- a feed nobody is polling on purpose has not gone stale,
    it has been put down.

    "Paused" and "disabled" used to be two states over two columns. They are one
    now: ``feed.polling`` is false either way, and the operator was doing the
    same thing both times.
    """
    if not policy or not policy.get("in_postgres"):
        return _status("unknown", "PostgreSQL state unavailable")
    if not policy.get("enabled"):
        return _status("paused", "feed.polling is false")

    strikes = policy.get("strikes") or 0
    if strikes >= BACKING_OFF_STRIKES:
        return _status("backing_off", f"{strikes} consecutive failed polls")

    last_poll_at = parse_iso(policy.get("last_poll_at"))
    if last_poll_at is None:
        return _status("never_polled", "no production poll recorded yet")

    interval_s = policy.get("interval_s") or 0
    age_s = (now - last_poll_at).total_seconds()
    if interval_s and age_s > STALE_INTERVAL_MULTIPLE * interval_s:
        return _status(
            "stale",
            f"last polled {humanize_seconds(age_s)} ago "
            f"at a {humanize_seconds(interval_s)} cadence",
        )

    return _status("healthy", "polling on cadence")
