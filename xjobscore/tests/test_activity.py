"""`activity.PollWindow` -- the in-memory poll counts behind the System page."""

from __future__ import annotations

from xjobscore import activity


def test_total_counts_every_recorded_poll_and_resets():
    window = activity.PollWindow()
    window.record(1, "ok", 0, now=600.0)
    window.record(2, "error", 0, now=601.0)
    window.record(3, "ok", 4, now=700.0)  # a later minute bucket
    assert window.total == 3

    window.reset()
    assert window.total == 0


def test_summary_sums_the_last_minutes_only():
    window = activity.PollWindow()
    window.started = 0.0
    window.record(1, "ok", 2, now=60.0)  # minute 1: outside a 15-minute window at t=1200
    window.record(2, "partial", 1, now=1140.0)
    window.record(3, "error", 0, now=1150.0)
    summary = window.summary(15, now=1200.0)
    assert summary["polled"] == 2
    assert summary["new_postings"] == 1
    assert summary["partial"] == 1
    assert summary["errors"] == 1
    assert summary["with_new"] == 1
    assert summary["since_start"] is False
