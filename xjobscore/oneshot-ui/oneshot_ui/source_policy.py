"""Pure policy for displaying and persisting explicit source fetches.

There is intentionally no cadence or due-time policy in OneShot.  A source is
contacted only when a user explicitly selects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xjobscore.records import Record
from xjobscore.work import Outcome, next_state

__all__ = ["ManualResultPolicy", "ParserState", "classify_result"]

ParserState = Literal["available", "missing", "error", "unknown"]
Health = Literal["healthy", "partial", "not_modified", "error", "missing_parser"]


@dataclass(frozen=True, slots=True)
class ManualResultPolicy:
    """The persisted fields derived from a completed manual attempt."""

    outcome: Outcome
    health: Health
    parser_state: ParserState
    state: dict


def classify_result(
    previous_state: dict,
    *,
    record: Record | None,
    parser_state: ParserState = "unknown",
    validators: dict | None = None,
) -> ManualResultPolicy:
    """Classify a manual response and roll validators/failure state.

    ``record=None`` represents a harvest error.  A missing parser is visible in
    health but does not turn a successful HTTP fetch into a retryable failure.
    """
    if record is None:
        outcome: Outcome = "error"
    elif record.truncated:
        outcome = "partial"
    elif record.http_status == 304:
        outcome = "not_modified"
    else:
        outcome = "ok"

    if outcome == "error":
        health: Health = "error"
    elif parser_state == "missing":
        health = "missing_parser"
    elif parser_state == "error":
        health = "error"
    elif outcome == "partial":
        health = "partial"
    elif outcome == "not_modified":
        health = "not_modified"
    else:
        health = "healthy"

    return ManualResultPolicy(
        outcome=outcome,
        health=health,
        parser_state=parser_state,
        state=next_state(previous_state, outcome, validators or {}),
    )
