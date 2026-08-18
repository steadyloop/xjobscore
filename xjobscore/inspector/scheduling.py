"""Pure derivation for the Scheduler page: arrival rate, cadence, status.

No I/O, no network, no wall-clock reads. Callers assemble the inputs and pass an
explicit ``now``, so everything here is unit-testable without a database.

**What λ means here.** λ is the estimated arrival rate of new postings per day,
computed as an exponentially-weighted average over *daily counts* read from
``poll_observation.new_postings`` -- not from per-posting source timestamps. The
difference is real: a posting created while nobody was looking is attributed to
the day we first saw it, so λ lags reality by up to one polling interval, and a
feed polled once a day reports a spike where the truth was a spread. Estimating
from source timestamps would mean joining ``job_posting`` back to ``feed`` across
the ``source = 'greenhouse:acme'`` string, which has no foreign key to stand on.
Daily counts are the cheap, honest approximation.

**Unknown is not zero.** Every estimate here degrades to ``None``, never to
``0.0``. A feed nobody has observed has an unknown rate, and rendering that as
zero would say "this feed produces nothing" -- the one conclusion an operator
must not draw from an empty table.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

# The cadence ladder, shared with the tier control on the Scheduler page.
# Fastest first.
INTERVAL_PRESETS = (300, 900, 3600, 21600, 86400)

# λ/day a feed must sustain to justify each interval. Read top-down: the first
# rung whose threshold the feed clears is the cadence it deserves.
LAMBDA_THRESHOLDS: tuple[tuple[int, float], ...] = (
    (300, 12.0),
    (900, 3.0),
    (3600, 0.8),
    (21600, 0.2),
    (86400, 0.0),
)

DEFAULT_HALF_LIFE_DAYS = 21.0
DEFAULT_PSEUDO_COUNT = 2.0
FALLBACK_PRIOR_LAMBDA = 1.0

# Below this many days of poll history a feed reads as "calibrating": λ exists
# but is too thin to argue with.
CALIBRATING_DAYS = 7
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
    "calibrating": "Calibrating",
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
    "calibrating": "not_modified",
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


def ewma_lambda(
    daily_counts: Sequence[int] | None,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float | None:
    """Postings/day, weighting each day by its own age.

    ``daily_counts`` is oldest-first and dense -- one entry per day in the
    window, explicit zeros included (see ``telemetry.feed_daily_counts``). An
    empty or missing sequence means no observation history, which is not a rate
    of zero, so the answer is ``None``.
    """
    if not daily_counts:
        return None
    last = len(daily_counts) - 1
    weighted = 0.0
    total_weight = 0.0
    for index, count in enumerate(daily_counts):
        weight = 0.5 ** ((last - index) / half_life_days)
        weighted += weight * count
        total_weight += weight
    if total_weight <= 0:
        return None
    return weighted / total_weight


def shrunk_lambda(
    lam: float | None,
    observed_days: int,
    prior: float,
    *,
    pseudo_count: float = DEFAULT_PSEUDO_COUNT,
) -> float | None:
    """Mix λ with the source prior, weighted by how much evidence backs it.

    Three days of history should not move a cadence recommendation on its own.
    ``pseudo_count`` days' worth of the prior is blended in, so a feed's own
    measurements take over gradually as they accumulate.
    """
    if lam is None:
        return None
    weight = max(0, observed_days)
    denominator = weight + pseudo_count
    if denominator <= 0:
        return prior
    return (lam * weight + prior * pseudo_count) / denominator


def source_priors(
    lambdas_by_source: dict[str, Sequence[float | None]],
) -> dict[str, float]:
    """Median λ per source -- the empirical-Bayes prior for its thin feeds.

    A source's own established feeds are a better prior for its new ones than any
    constant: Ashby tenants resemble other Ashby tenants more than they resemble
    a global average. Sources where nothing is known yet are simply absent; use
    ``prior_for`` to pick up the fallback.
    """
    priors: dict[str, float] = {}
    for source, values in lambdas_by_source.items():
        known = sorted(value for value in values if value is not None)
        if not known:
            continue
        middle = len(known) // 2
        priors[source] = (
            known[middle]
            if len(known) % 2
            else (known[middle - 1] + known[middle]) / 2
        )
    return priors


def prior_for(
    source: str,
    priors: dict[str, float],
    *,
    fallback: float = FALLBACK_PRIOR_LAMBDA,
) -> float:
    return priors.get(source, fallback)


def ideal_interval_s(lam: float | None) -> int | None:
    """The fastest preset cadence the estimated rate justifies."""
    if lam is None:
        return None
    for interval_s, threshold in LAMBDA_THRESHOLDS:
        if lam >= threshold:
            return interval_s
    return INTERVAL_PRESETS[-1]


def interval_verdict(current_s: int | None, ideal_s: int | None) -> str | None:
    """Compare configured cadence against the one λ argues for.

    Advisory only -- this page recommends, it never rewrites ``interval_s``.
    """
    if not current_s or not ideal_s:
        return None
    if current_s == ideal_s:
        return "ok"
    return "too_short" if current_s < ideal_s else "too_long"


def requests_per_day(interval_s: int | None) -> float | None:
    """Request cost of a cadence -- the other half of every interval decision."""
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
    telemetry: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """One pill folding together enabled, paused_at, strikes, staleness, history.

    Ordered most-severe first and first-match-wins: a paused feed is not also
    reported as stale, because the operator only needs the reason that actually
    explains what they are looking at. Deliberate intent outranks every derived
    signal below it -- a feed nobody is polling on purpose has not gone stale,
    it has been put down.
    """
    if not policy or not policy.get("in_postgres"):
        return _status("unknown", "PostgreSQL state unavailable")
    if not policy.get("enabled"):
        return _status("disabled", "feed.enabled is false")
    if policy.get("paused_at"):
        return _status("paused", f"polling held since {policy['paused_at']}")

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

    observed_days = (telemetry or {}).get("observed_days") or 0
    if observed_days < CALIBRATING_DAYS:
        return _status(
            "calibrating",
            f"{observed_days} of {CALIBRATING_DAYS} days of poll history",
        )
    return _status("healthy", "polling on cadence")
