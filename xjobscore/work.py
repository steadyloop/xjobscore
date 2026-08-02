"""What runs next and when. Queue and backoff in one pure function set.

Nothing here reads the clock, opens a connection or performs I/O -- `now` is an
argument and every function is a pure mapping from state to decision. That is
what makes the scheduling policy testable without a database and what keeps a
recurring feed and a one-off Target on one code path: they differ only in what
`next_poll_at` returns when the work is done.

The picker query never learns about any of this. It reads one column,
`feed.next_poll_at`, and every policy below is a different way of computing it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

from xjobscore.records import Feed

__all__ = [
    "MAX_BACKOFF_S",
    "Outcome",
    "backoff_s",
    "next_poll_at",
    "next_state",
    "pick",
    "should_reconcile",
]

# What one poll came to. 'not_modified' is a success -- a 304 is the cheapest
# possible correct answer -- but it is distinct from 'ok' because there is no
# listing to reconcile against. 'partial' is a fetch that died mid-pagination.
Outcome = Literal["ok", "not_modified", "partial", "error"]

# A dead board should be retried daily, not never and not every 15 minutes.
MAX_BACKOFF_S = 86_400


def backoff_s(cadence_s: int, consecutive_failures: int) -> int:
    """cadence * 2**failures, capped at MAX_BACKOFF_S.

    Zero failures returns cadence unchanged, so the happy path needs no branch
    at the call site.
    """
    return min(cadence_s * 2**consecutive_failures, MAX_BACKOFF_S)


def _failure_count(state: dict) -> int:
    """The stored counter, tolerant of anything that isn't a clean int.

    A corrupt or missing value must not blow up scheduling -- it is treated as
    zero prior failures, same as a feed that has never failed.
    """
    value = state.get("consecutive_failures", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _rolled_failures(state: dict, outcome: Outcome) -> int:
    """consecutive_failures after this poll: reset on success, +1 otherwise."""
    if outcome in ("ok", "not_modified"):
        return 0
    return _failure_count(state) + 1


def next_poll_at(feed: Feed, outcome: Outcome, now: datetime) -> datetime | None:
    """When this feed comes due again. None means never.

    None is how a one-off Target retires: it is dispatched, it succeeds, and it
    stops being due -- without a second table, a second query or a delete.
    A one-off that *failed* still backs off and retries; retiring on error
    would lose the work silently.

    A feed carries no `once` column of its own -- a recurring feed and a
    one-off Target are the same `feed` row, so the flag lives in the same
    place the other not-yet-columned capabilities do: `feed.config["once"]`.
    """
    once = bool(feed.config.get("once", False))
    if once and outcome in ("ok", "not_modified"):
        return None

    if outcome in ("partial", "error"):
        delay = backoff_s(feed.cadence_s, _rolled_failures(feed.state, outcome))
    else:
        delay = feed.cadence_s
    return now + timedelta(seconds=delay)


def next_state(state: dict, outcome: Outcome, validators: dict) -> dict:
    """The feed's next state jsonb. Returns a new dict; never mutates.

    Rolls consecutive_failures (reset on ok/not_modified, incremented on
    partial/error) and merges in whatever validators the response carried.
    Merges rather than replaces: a 304 need not repeat the ETag it matched, so
    overwriting would throw away the validator that just worked.
    """
    new_state = dict(state)
    new_state["consecutive_failures"] = _rolled_failures(state, outcome)
    new_state.update(validators)
    return new_state


def should_reconcile(feed: Feed, outcome: Outcome) -> bool:
    """Whether closure-by-absence may run for this poll.

    True only when the operator flagged the feed `complete` and `closes` AND
    the poll finished 'ok'. Every other combination is a way to delete postings
    that are still open: one 500 must never close 800 jobs, a half-paged board
    is not evidence, and a 304 listed nothing at all.
    """
    return bool(feed.complete and feed.closes and outcome == "ok")


def pick(feeds: Sequence[Feed], now: datetime, limit: int) -> list[Feed]:
    """Order an already-fetched set of feeds and take the first `limit`.

    Due ordering with never-run first, mirroring `feed_due_idx`. Exists so the
    ordering rule can be tested as a pure function and so a caller holding
    feeds in memory -- a dry run, a backfill -- schedules them exactly the way
    the database would.
    """

    def sort_key(feed: Feed) -> tuple[bool, datetime]:
        # never-run (next_poll_at is None) sorts first, same as NULLS FIRST
        return (feed.next_poll_at is not None, feed.next_poll_at or now)

    return sorted(feeds, key=sort_key)[:limit]
