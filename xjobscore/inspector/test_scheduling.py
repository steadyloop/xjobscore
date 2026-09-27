"""Unit tests for scheduling.py -- pure functions, no database, no clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def test_requests_per_day():
    assert scheduling.requests_per_day(3600) == 24
    assert scheduling.requests_per_day(7 * 86_400) == 1 / 7
    assert scheduling.requests_per_day(None) is None


# ---------------------------------------------------------------- status

def test_degraded_policy_reads_as_unknown():
    assert scheduling.feed_status(None, NOW)["state"] == "unknown"
    assert scheduling.feed_status({"in_postgres": False}, NOW)["state"] == "unknown"


def test_pause_outranks_strikes():
    """Deliberate intent explains the row; a strike count underneath does not."""
    status = scheduling.feed_status(policy(enabled=False, strikes=9), NOW)
    assert status["state"] == "paused"


def test_paused_feed_is_not_reported_as_stale():
    """The whole point of a hold: due_at goes stale on purpose, so staleness lies."""
    held = policy(
        enabled=False,
        last_poll_at=(NOW - timedelta(days=9)).isoformat(),
        interval_s=900,
    )
    assert scheduling.feed_status(held, NOW)["state"] == "paused"


def test_pause_and_disable_are_one_state():
    """Two columns became one: polling = false is the only way to be held."""
    off = policy(enabled=False)
    assert scheduling.feed_status(off, NOW)["state"] == "paused"
    assert scheduling.feed_status(off, NOW)["detail"] == "feed.polling is false"


def test_strikes_read_as_backing_off():
    assert scheduling.feed_status(policy(strikes=3), NOW)["state"] == "backing_off"


def test_never_polled():
    assert (
        scheduling.feed_status(policy(last_poll_at=None), NOW)["state"]
        == "never_polled"
    )


def test_stale_when_far_past_the_cadence():
    stale = policy(last_poll_at=(NOW - timedelta(hours=4)).isoformat(), interval_s=900)
    assert scheduling.feed_status(stale, NOW)["state"] == "stale"


def test_fresh_feed_is_healthy_without_a_calibration_period():
    assert scheduling.feed_status(policy(), NOW)["state"] == "healthy"


def test_custom_interval_controls_staleness_threshold():
    custom = policy(
        interval_s=5400,
        last_poll_at=(NOW - timedelta(hours=4, minutes=29)).isoformat(),
    )
    assert scheduling.feed_status(custom, NOW)["state"] == "healthy"
    custom["last_poll_at"] = (NOW - timedelta(hours=4, minutes=31)).isoformat()
    assert scheduling.feed_status(custom, NOW)["state"] == "stale"


def test_held_feed_is_not_reported_as_stale():
    off = policy(enabled=False, last_poll_at=(NOW - timedelta(days=9)).isoformat())
    assert scheduling.feed_status(off, NOW)["state"] == "paused"


def test_every_state_maps_to_a_styled_chip():
    """States are rendered as .chip[data-status]; components.css styles five."""
    styled = {"ok", "unchanged", "partial", "error", "never"}
    assert set(scheduling.STATE_CHIPS) == set(scheduling.STATE_LABELS)
    assert set(scheduling.STATE_CHIPS.values()) <= styled
