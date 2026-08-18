"""Pure scheduling policy over schema.sql's ``feed`` columns."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

__all__ = [
    "MAX_BACKOFF_S",
    "Outcome",
    "backoff_s",
    "next_due_at",
    "next_strikes",
]

Outcome = Literal["ok", "not_modified", "partial", "error"]
MAX_BACKOFF_S = 86_400


def backoff_s(interval_s: int, strikes: int) -> int:
    """Return exponential retry delay, capped at one day."""
    return min(interval_s * 2 ** min(max(0, strikes), 31), MAX_BACKOFF_S)


def next_strikes(current: int, outcome: Outcome) -> int:
    """Successful fetches recover; partial and failed work accumulates strikes."""
    return 0 if outcome in ("ok", "not_modified") else min(max(0, current) + 1, 32767)


def next_due_at(
    interval_s: int,
    strikes: int,
    outcome: Outcome,
    now: datetime,
) -> datetime:
    """Calculate ``feed.due_at`` after one attempt."""
    rolled = next_strikes(strikes, outcome)
    delay = backoff_s(interval_s, rolled) if outcome in ("partial", "error") else interval_s
    return now + timedelta(seconds=delay)
