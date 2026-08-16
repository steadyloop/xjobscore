"""Unit tests for scheduling.py -- pure functions, no database, no clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.inspector import scheduling

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def policy(**overrides):
    base = {
        "in_postgres": True,
        "enabled": True,
        "interval_s": 900,
        "strikes": 0,
        "paused_at": None,
        "last_poll_at": (NOW - timedelta(minutes=5)).isoformat(),
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------- ewma

def test_ewma_of_a_flat_history_is_the_daily_rate():
    assert scheduling.ewma_lambda([2] * 14) == pytest.approx(2.0)


def test_ewma_weights_recent_days_more_heavily():
    recent = scheduling.ewma_lambda([0] * 13 + [14])
    old = scheduling.ewma_lambda([14] + [0] * 13)
    assert recent > old


def test_ewma_halves_weight_over_one_half_life():
    # A single posting today vs the same posting one half-life ago.
    today = scheduling.ewma_lambda([0] * 21 + [1], half_life_days=21.0)
    ago = scheduling.ewma_lambda([1] + [0] * 21, half_life_days=21.0)
    assert ago == pytest.approx(today * 0.5, rel=1e-6)


def test_no_history_is_unknown_not_zero():
    assert scheduling.ewma_lambda([]) is None
    assert scheduling.ewma_lambda(None) is None


def test_observed_but_silent_is_genuinely_zero():
    """Polled every day, never a new posting -- that is a real rate of zero."""
    assert scheduling.ewma_lambda([0] * 14) == 0.0


# -------------------------------------------------------------- shrinkage

def test_shrinkage_pulls_thin_history_toward_the_prior():
    lam = scheduling.shrunk_lambda(10.0, observed_days=1, prior=1.0, pseudo_count=2.0)
    assert lam == pytest.approx((10.0 * 1 + 1.0 * 2) / 3)
    assert lam < 10.0


def test_shrinkage_fades_as_evidence_accumulates():
    thin = scheduling.shrunk_lambda(10.0, 1, 1.0)
    thick = scheduling.shrunk_lambda(10.0, 60, 1.0)
    assert abs(thick - 10.0) < abs(thin - 10.0)


def test_shrinkage_keeps_unknown_unknown():
    assert scheduling.shrunk_lambda(None, 30, 1.0) is None


# ----------------------------------------------------------------- priors

def test_source_prior_is_the_median_of_that_sources_feeds():
    priors = scheduling.source_priors({"ashby": [1.0, 5.0, 3.0], "lever": [None]})
    assert priors["ashby"] == 3.0
    assert "lever" not in priors  # nothing known yet


def test_prior_falls_back_when_the_source_is_unknown():
    assert scheduling.prior_for("brand-new", {}) == scheduling.FALLBACK_PRIOR_LAMBDA


# --------------------------------------------------------------- cadence

@pytest.mark.parametrize(
    "lam,expected",
    [(20.0, 300), (12.0, 300), (5.0, 900), (1.0, 3600), (0.5, 21600), (0.0, 86400)],
)
def test_ideal_interval_hits_every_rung(lam, expected):
    assert scheduling.ideal_interval_s(lam) == expected


def test_ideal_interval_is_unknown_without_a_rate():
    assert scheduling.ideal_interval_s(None) is None


def test_interval_verdict():
    assert scheduling.interval_verdict(900, 900) == "ok"
    assert scheduling.interval_verdict(300, 3600) == "too_short"
    assert scheduling.interval_verdict(86400, 900) == "too_long"
    assert scheduling.interval_verdict(None, 900) is None
    assert scheduling.interval_verdict(900, None) is None


def test_requests_per_day():
    assert scheduling.requests_per_day(3600) == 24
    assert scheduling.requests_per_day(None) is None


# ---------------------------------------------------------------- status

def test_degraded_policy_reads_as_unknown():
    assert scheduling.feed_status(None, None, NOW)["state"] == "unknown"
    assert scheduling.feed_status({"in_postgres": False}, None, NOW)["state"] == "unknown"


def test_pause_outranks_strikes():
    """Deliberate intent explains the row; a strike count underneath does not."""
    status = scheduling.feed_status(
        policy(paused_at=NOW.isoformat(), strikes=9), None, NOW
    )
    assert status["state"] == "paused"


def test_paused_feed_is_not_reported_as_stale():
    """The whole point of a hold: due_at goes stale on purpose, so staleness lies."""
    held = policy(
        paused_at=(NOW - timedelta(days=3)).isoformat(),
        last_poll_at=(NOW - timedelta(days=9)).isoformat(),
        interval_s=900,
    )
    assert scheduling.feed_status(held, None, NOW)["state"] == "paused"


def test_disabled_outranks_paused():
    """A feed switched off on the Feeds page is off, held or not."""
    off = policy(enabled=False, paused_at=NOW.isoformat())
    assert scheduling.feed_status(off, None, NOW)["state"] == "disabled"


def test_strikes_read_as_backing_off():
    assert scheduling.feed_status(policy(strikes=3), None, NOW)["state"] == "backing_off"


def test_never_polled():
    assert (
        scheduling.feed_status(policy(last_poll_at=None), None, NOW)["state"]
        == "never_polled"
    )


def test_stale_when_far_past_the_cadence():
    stale = policy(last_poll_at=(NOW - timedelta(hours=4)).isoformat(), interval_s=900)
    assert scheduling.feed_status(stale, None, NOW)["state"] == "stale"


def test_thin_history_reads_as_calibrating_not_healthy():
    telemetry = {"observed_days": 2}
    assert scheduling.feed_status(policy(), telemetry, NOW)["state"] == "calibrating"


def test_healthy_needs_both_a_fresh_poll_and_enough_history():
    telemetry = {"observed_days": scheduling.CALIBRATING_DAYS}
    assert scheduling.feed_status(policy(), telemetry, NOW)["state"] == "healthy"


def test_disabled_feed_is_not_reported_as_stale():
    off = policy(enabled=False, last_poll_at=(NOW - timedelta(days=9)).isoformat())
    assert scheduling.feed_status(off, None, NOW)["state"] == "disabled"


def test_every_state_maps_to_a_styled_chip():
    """States are rendered as .chip[data-status]; components.css styles five."""
    styled = {"ok", "not_modified", "partial", "error", "never"}
    assert set(scheduling.STATE_CHIPS) == set(scheduling.STATE_LABELS)
    assert set(scheduling.STATE_CHIPS.values()) <= styled
