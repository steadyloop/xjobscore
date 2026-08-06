"""Pure posting-observation and unified-entity lifecycle resolution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

LIFECYCLE_VERSION = "posting-lifecycle/1.0"


class LifecycleState(str, Enum):
    OPEN = "open"
    UNLISTED = "unlisted"
    CLOSED = "closed"
    CONFLICTING = "conflicting"
    QUARANTINED = "quarantined"


class Authority(str, Enum):
    CANONICAL_EMPLOYER = "canonical_employer"
    EMPLOYER = "employer"
    AGGREGATOR = "aggregator"

    @property
    def authoritative(self) -> bool:
        return self is not Authority.AGGREGATOR


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    at: datetime
    state: LifecycleState
    reason: str
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationLifecycle:
    source_instance: str
    external_id: str
    authority: Authority
    state: LifecycleState = LifecycleState.OPEN
    consecutive_absences: int = 0
    absent_since: datetime | None = None
    last_seen_at: datetime | None = None
    last_seen_poll_run_id: str | None = None
    absence_poll_run_id: str | None = None
    explicit_closed: bool = False
    history: tuple[LifecycleEvent, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.source_instance, self.external_id


@dataclass(frozen=True, slots=True)
class Snapshot:
    feed_id: str
    run_id: str
    observed_ids: frozenset[str]
    polled_at: datetime
    complete: bool
    closes: bool
    truncated: bool = False
    detail_fetches_complete: bool = True
    sanity_passed: bool = True
    not_modified: bool = False
    reliable_authoritative: bool = False


@dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    minimum_baseline_for_drop_check: int = 10
    quarantine_drop_fraction: float = 0.80

    def __post_init__(self) -> None:
        if self.minimum_baseline_for_drop_check < 1:
            raise ValueError("minimum_baseline_for_drop_check must be positive")
        if not 0 < self.quarantine_drop_fraction <= 1:
            raise ValueError("quarantine_drop_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class SnapshotResolution:
    observations: tuple[ObservationLifecycle, ...]
    state: LifecycleState
    reason: str
    vanished_ids: tuple[str, ...] = ()
    reopened_ids: tuple[str, ...] = ()
    algorithm_version: str = LIFECYCLE_VERSION


@dataclass(frozen=True, slots=True)
class EntityResolution:
    state: LifecycleState
    reason: str
    conflicting_observations: tuple[tuple[str, str], ...] = ()
    algorithm_version: str = LIFECYCLE_VERSION


def _event(
    observation: ObservationLifecycle,
    *,
    at: datetime,
    state: LifecycleState,
    reason: str,
    run_id: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    return observation.history + (LifecycleEvent(at, state, reason, run_id),)


def apply_explicit_status(
    observation: ObservationLifecycle,
    state: LifecycleState,
    *,
    at: datetime,
    evidence: str,
    run_id: str | None = None,
) -> ObservationLifecycle:
    """Apply direct source assertions and retain their audit history."""

    if state not in {LifecycleState.OPEN, LifecycleState.CLOSED}:
        raise ValueError("explicit source status must be open or closed")
    if not evidence.strip():
        raise ValueError("explicit status requires evidence")
    if state is LifecycleState.CLOSED:
        return replace(
            observation,
            state=state,
            explicit_closed=True,
            consecutive_absences=0,
            absence_poll_run_id=None,
            history=_event(observation, at=at, state=state, reason=evidence, run_id=run_id),
        )
    return replace(
        observation,
        state=LifecycleState.OPEN,
        explicit_closed=False,
        consecutive_absences=0,
        absent_since=None,
        absence_poll_run_id=None,
        last_seen_at=at,
        last_seen_poll_run_id=run_id,
        history=_event(observation, at=at, state=LifecycleState.OPEN, reason=f"reopened: {evidence}", run_id=run_id),
    )


def snapshot_quarantine_reason(
    previous: Snapshot | None,
    current: Snapshot,
    policy: SnapshotPolicy = SnapshotPolicy(),
) -> str | None:
    if not current.sanity_passed:
        return "snapshot failed source-specific sanity checks"
    if previous is None:
        return None
    baseline = len(previous.observed_ids)
    if baseline < policy.minimum_baseline_for_drop_check:
        return None
    disappeared = len(previous.observed_ids - current.observed_ids)
    fraction = disappeared / baseline
    if fraction >= policy.quarantine_drop_fraction:
        return f"suspicious snapshot drop of {disappeared}/{baseline} ({fraction:.1%})"
    return None


def reconcile_snapshots(
    previous: Snapshot | None,
    current: Snapshot,
    observations: tuple[ObservationLifecycle, ...],
    *,
    policy: SnapshotPolicy = SnapshotPolicy(),
) -> SnapshotResolution:
    """Resolve first/second absence, reopen, and suspicious snapshots.

    First valid absence becomes ``unlisted``.  A less reliable feed closes on
    the second complete absence; a feed explicitly marked reliable closes on
    the first.  A present ID reopens while preserving history.
    """

    if current.not_modified:
        return SnapshotResolution(observations, LifecycleState.OPEN, "304 not modified; no membership inference")
    if not current.complete or not current.closes or current.truncated or not current.detail_fetches_complete:
        return SnapshotResolution(observations, LifecycleState.OPEN, "snapshot is not eligible for absence inference")
    quarantine = snapshot_quarantine_reason(previous, current, policy)
    if quarantine:
        return SnapshotResolution(observations, LifecycleState.QUARANTINED, quarantine)
    if previous is None:
        return SnapshotResolution(observations, LifecycleState.OPEN, "first valid snapshot establishes membership")

    updated: list[ObservationLifecycle] = []
    vanished: list[str] = []
    reopened: list[str] = []
    previous_ids = previous.observed_ids

    for observation in observations:
        if observation.source_instance != current.feed_id:
            updated.append(observation)
            continue
        if observation.external_id in current.observed_ids:
            if observation.state is not LifecycleState.OPEN or observation.consecutive_absences:
                reopened.append(observation.external_id)
                history = _event(
                    observation,
                    at=current.polled_at,
                    state=LifecycleState.OPEN,
                    reason="source ID returned in a valid complete snapshot",
                    run_id=current.run_id,
                )
            else:
                history = observation.history
            updated.append(
                replace(
                    observation,
                    state=LifecycleState.OPEN,
                    explicit_closed=False,
                    consecutive_absences=0,
                    absent_since=None,
                    absence_poll_run_id=None,
                    last_seen_at=current.polled_at,
                    last_seen_poll_run_id=current.run_id,
                    history=history,
                )
            )
            continue

        # A newly vanished ID, or an already-unlisted observation that remains
        # absent, is eligible.  Never infer absence for an unrelated row that
        # was not a member of the previous snapshot.
        if observation.external_id not in previous_ids and observation.consecutive_absences == 0:
            updated.append(observation)
            continue
        vanished.append(observation.external_id)
        absences = observation.consecutive_absences + 1
        close = current.reliable_authoritative or absences >= 2
        state = LifecycleState.CLOSED if close else LifecycleState.UNLISTED
        reason = "reliable authoritative first absence" if close and absences == 1 else f"valid complete snapshot absence #{absences}"
        updated.append(
            replace(
                observation,
                state=state,
                explicit_closed=False,
                consecutive_absences=absences,
                absent_since=observation.absent_since or current.polled_at,
                absence_poll_run_id=current.run_id,
                history=_event(observation, at=current.polled_at, state=state, reason=reason, run_id=current.run_id),
            )
        )

    return SnapshotResolution(
        tuple(updated),
        LifecycleState.OPEN,
        "valid complete snapshot reconciled",
        tuple(sorted(vanished)),
        tuple(sorted(reopened)),
    )


def resolve_entity(observations: tuple[ObservationLifecycle, ...]) -> EntityResolution:
    """Resolve a unified role without overwriting observation-level facts."""

    if not observations:
        return EntityResolution(LifecycleState.UNLISTED, "no posting observations")

    authoritative = tuple(item for item in observations if item.authority.authoritative)
    canonical_closed = tuple(
        item
        for item in authoritative
        if item.authority is Authority.CANONICAL_EMPLOYER
        and item.state is LifecycleState.CLOSED
        and item.explicit_closed
    )
    employer_open = tuple(item for item in authoritative if item.state is LifecycleState.OPEN)
    explicit_employer_closed = tuple(
        item for item in authoritative if item.state is LifecycleState.CLOSED and item.explicit_closed
    )

    if canonical_closed:
        other_open = tuple(item for item in employer_open if item.key not in {closed.key for closed in canonical_closed})
        if other_open:
            conflicts = tuple(sorted(item.key for item in (*canonical_closed, *other_open)))
            return EntityResolution(
                LifecycleState.CONFLICTING,
                "independent employer-owned sources disagree with canonical explicit closure",
                conflicts,
            )
        return EntityResolution(
            LifecycleState.CLOSED,
            "canonical employer apply endpoint explicitly closed; aggregator copies cannot keep it open",
        )

    if employer_open and explicit_employer_closed:
        conflicts = tuple(sorted(item.key for item in (*employer_open, *explicit_employer_closed)))
        return EntityResolution(
            LifecycleState.CONFLICTING,
            "authoritative employer-owned sources conflict",
            conflicts,
        )
    if employer_open:
        return EntityResolution(LifecycleState.OPEN, "at least one authoritative origin is open")
    if authoritative and all(item.state in {LifecycleState.UNLISTED, LifecycleState.CLOSED} for item in authoritative):
        return EntityResolution(LifecycleState.CLOSED, "all authoritative origins are unlisted or closed")

    # With no origin, copies may describe what is visible but cannot outrank an
    # origin.  Preserve the weaker inference in the reason.
    if any(item.state is LifecycleState.OPEN for item in observations):
        return EntityResolution(LifecycleState.OPEN, "only a non-authoritative copy is open; origin verification needed")
    if any(item.state is LifecycleState.CLOSED and item.explicit_closed for item in observations):
        return EntityResolution(LifecycleState.CLOSED, "only non-authoritative explicit closure evidence is available")
    return EntityResolution(LifecycleState.UNLISTED, "all available copies are absent")


__all__ = [
    "LIFECYCLE_VERSION",
    "Authority",
    "EntityResolution",
    "LifecycleEvent",
    "LifecycleState",
    "ObservationLifecycle",
    "Snapshot",
    "SnapshotPolicy",
    "SnapshotResolution",
    "apply_explicit_status",
    "reconcile_snapshots",
    "resolve_entity",
    "snapshot_quarantine_reason",
]
