"""Unit tests for the shared Scheduler board -- no database, no app."""

from __future__ import annotations

import asyncio

from xjobscore.console.board import Board, Shared
from xjobscore.console.db import feed_ref

A, B, C = (feed_ref("demo", key) for key in "abc")


def _row(ref, key, state="healthy", source="demo", **extra):
    return {
        "feed_ref": ref,
        "source": source,
        "key": key,
        "company_name": key.upper(),
        "sched": {"status": {"state": state}},
        **extra,
    }


def _board() -> Board:
    lookalike = ',{"feed_ref":"' + A
    rows = [_row(ref, key, note=lookalike) for ref, key in ((A, "a"), (B, "b"), (C, "c"))]
    return Board.build(rows, telemetry_available=True)


class Loader:
    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()
        self.gate.set()
        self.fail = False

    async def __call__(self) -> int:
        self.calls += 1
        number = self.calls  # which load this is, fixed when it starts
        await self.gate.wait()
        if self.fail:
            raise RuntimeError("boom")
        return number


async def test_concurrent_callers_share_one_load():
    load = Loader()
    load.gate.clear()
    shared = Shared(load, ttl_s=60)
    waiting = [asyncio.create_task(shared.get()) for _ in range(5)]
    await asyncio.sleep(0)
    load.gate.set()
    assert await asyncio.gather(*waiting) == [1] * 5
    assert await shared.get() == 1
    assert load.calls == 1


async def test_a_load_started_before_invalidate_is_never_stored():
    load = Loader()
    load.gate.clear()
    shared = Shared(load, ttl_s=60)
    before = asyncio.create_task(shared.get())
    await asyncio.sleep(0)
    shared.invalidate()  # a write lands while the first load is in flight
    after = asyncio.create_task(shared.get())
    await asyncio.sleep(0)
    load.gate.set()
    assert await before == 1
    assert await after == 2  # the post-write caller got its own load
    assert await shared.get() == 2
    assert load.calls == 2


async def test_serve_stale_answers_at_once_and_refreshes_behind():
    load = Loader()
    shared = Shared(load, ttl_s=0, serve_stale=True)
    assert await shared.get() == 1  # nothing to serve yet: waits
    load.gate.clear()
    assert await shared.get() == 1  # expired, but answered without waiting
    load.gate.set()
    await asyncio.sleep(0.01)
    assert await shared.get() == 2
    await asyncio.sleep(0.01)  # let the refresh that read started finish


async def test_a_failed_load_is_retried_next_time():
    load = Loader()
    load.fail = True
    shared = Shared(load, ttl_s=60)
    try:
        await shared.get()
    except RuntimeError:
        pass
    else:
        raise AssertionError("the failure must reach the caller")
    load.fail = False
    assert await shared.get() == 2


def test_board_keeps_each_vendor_whole_and_finds_each_row():
    board = _board()
    assert [row["feed_ref"] for row in board.rows("demo")] == [A, B, C]
    assert board.rows("missing") is None
    assert board.feed_count == 3 and board.counts == {"healthy": 3}
    assert board.telemetry_available is True
    # First, middle, and last rows -- the last one ends at the array's `]`.
    # Every row's string carries a lookalike boundary; escaping keeps it inert.
    assert [board.row(ref)["key"] for ref in (A, B, C)] == ["a", "b", "c"]
    assert board.row(B, "demo")["key"] == "b"
    assert board.row(B, "other") is None


def test_board_row_rejects_unknown_and_malformed_refs():
    board = _board()
    assert board.row(feed_ref("demo", "missing")) is None
    assert board.row("nope:nope") is None
    assert board.row('"}') is None


def test_board_summary_rolls_up_each_status_worst_first():
    D = feed_ref("demo", "d")
    policy = lambda interval, due, last, enabled=True: {  # noqa: E731
        "interval_s": interval, "due_at": due, "last_poll_at": last, "enabled": enabled,
    }
    rows = [
        _row(A, "a", "backing_off", policy=policy(900, "2026-10-10T01:00:00+00:00", "2026-10-09T00:00:00+00:00"),
             spark=[9, 2], telemetry={"error_count_7d": 4}),
        _row(B, "b", "backing_off", policy=policy(3600, "2026-10-10T00:30:00+00:00", "2026-10-09T05:00:00+00:00"),
             spark=[0, 1], telemetry={"error_count_7d": 3}),
        _row(C, "c", "healthy", policy=policy(900, "2026-10-10T02:00:00+00:00", "2026-10-10T00:00:00+00:00"),
             spark=None, telemetry=None),
        # A held feed has no next poll: it must not pull the vendor's "due".
        _row(D, "d", "paused", policy=policy(86_400, "2026-01-01T00:00:00+00:00", None, enabled=False)),
    ]
    summary = Board.build(rows, telemetry_available=True).summary["demo"]
    assert summary["counts"] == {"backing_off": 2, "healthy": 1, "paused": 1}
    err, paused, ok = summary["buckets"]
    assert [err["state"], paused["state"], ok["state"]] == ["backing_off", "paused", "healthy"]
    assert (err["count"], err["interval_min"], err["interval_max"]) == (2, 900, 3600)
    assert err["due_min"] == "2026-10-10T00:30:00+00:00"
    assert (err["last_min"], err["last_max"]) == ("2026-10-09T00:00:00+00:00", "2026-10-09T05:00:00+00:00")
    assert (err["jobs"], err["errors"]) == (3, 7)  # today's new postings, 7-day errors
    assert paused["due_min"] is None and paused["last_max"] is None
    assert (ok["jobs"], ok["errors"]) == (0, 0)

    blind = Board.build(rows, telemetry_available=False).summary["demo"]["buckets"][0]
    assert blind["jobs"] is None and blind["errors"] is None


def test_board_search_counts_rows_once_and_caps_what_it_returns():
    rows = [_row(feed_ref("wd", f"k{i}"), f"nvidia{i}", source="wd") for i in range(30)]
    rows.append(_row(feed_ref("gh", "x"), "acme", source="gh", company_name="Nvidia Labs\nNVIDIA"))
    board = Board.build(rows, telemetry_available=False)
    found = board.search("NVIDIA", limit=25)
    assert found["wd"] == (30, list(range(25)))
    assert found["gh"] == (1, [0])  # two hits on one row count once
    assert board.search("acme", 25) == {"gh": (1, [0])}
    assert board.search("", 25) == {} and board.search("zzz", 25) == {}
