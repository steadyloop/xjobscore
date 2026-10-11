"""Unit tests for the shared Scheduler board -- no database, no app."""

from __future__ import annotations

import asyncio
import gzip

import orjson

from xjobscore.console.board import Board, Shared
from xjobscore.console.db import feed_ref

A, B, C = (feed_ref("demo", key) for key in "abc")


def _board() -> Board:
    rows = [
        {"feed_ref": ref, "source": "demo", "key": key, "note": ',{"feed_ref":"' + A}
        for ref, key in ((A, "a"), (B, "b"), (C, "c"))
    ]
    return Board.build(
        {"postgres": {"connected": True}, "telemetry_available": True, "feeds": rows},
        {"healthy": 3},
    )


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


def test_board_body_round_trips_and_finds_each_row():
    board = _board()
    body = orjson.loads(gzip.decompress(board.gz))
    assert [row["feed_ref"] for row in body["feeds"]] == [A, B, C]
    assert board.feed_count == 3 and board.counts == {"healthy": 3}
    assert board.telemetry_available is True
    # First, middle, and last rows -- the last one ends at the array's `]`.
    # Every row's string carries a lookalike boundary; escaping keeps it inert.
    assert [board.row(ref)["key"] for ref in (A, B, C)] == ["a", "b", "c"]


def test_board_row_rejects_unknown_and_malformed_refs():
    board = _board()
    assert board.row(feed_ref("demo", "missing")) is None
    assert board.row("nope:nope") is None
    assert board.row('"}') is None
