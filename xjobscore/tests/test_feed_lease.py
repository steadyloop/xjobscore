"""Long harvests retain their claim, and finalization uses its newest token."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore import store
from xjobscore.feed_lease import FeedLease, FeedLeaseLost
from xjobscore.records import Feed


class Pool:
    @asynccontextmanager
    async def acquire(self):
        yield object()


def lease():
    now = datetime.now(timezone.utc)
    feed = Feed(id=1, source='workday', key='example/wd1/jobs', hold_until=now + timedelta(hours=1))
    result = FeedLease(Pool(), feed, now)
    result.interval = .001
    return result


async def test_renewal_settles_before_returning_the_new_claim_token(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    owner = lease()
    original = owner.feed.hold_until
    deadlines = []

    async def renew(conn, feed, *, now, until):
        assert feed.hold_until == original
        deadlines.append(until)
        entered.set()
        await release.wait()
        return True

    async def harvest():
        await entered.wait()
        return 'inventory'

    monkeypatch.setattr(store, 'renew_feed_lease', renew)
    task = asyncio.create_task(owner.run(harvest))
    await entered.wait()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    assert await task == 'inventory'
    assert owner.feed.hold_until == deadlines[0]
    assert owner.feed.hold_until > original


async def test_lost_ownership_cancels_the_harvest(monkeypatch):
    cancelled = asyncio.Event()
    owner = lease()

    async def renew(*args, **kwargs):
        return False

    async def harvest():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(store, 'renew_feed_lease', renew)
    with pytest.raises(FeedLeaseLost):
        await owner.run(harvest)
    assert cancelled.is_set()


async def test_parent_cancellation_settles_an_inflight_renewal(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    owner = lease()
    deadlines = []

    async def renew(conn, feed, *, now, until):
        deadlines.append(until)
        entered.set()
        await release.wait()
        return True

    async def harvest():
        await asyncio.Event().wait()

    monkeypatch.setattr(store, 'renew_feed_lease', renew)
    task = asyncio.create_task(owner.run(harvest))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.feed.hold_until == deadlines[0]


async def test_unclaimed_harvest_does_not_renew(monkeypatch):
    async def renew(*args, **kwargs):
        raise AssertionError('No claim to renew')

    async def harvest():
        return 7

    monkeypatch.setattr(store, 'renew_feed_lease', renew)
    owner = FeedLease(Pool(), Feed(id=1, source='workday', key='test/wd1/jobs'), datetime.now(timezone.utc))
    assert await owner.run(harvest) == 7


async def test_store_renewal_requires_the_same_unexpired_owner():
    class Conn:
        async def execute(self, query, *args, **kwargs):
            assert 'hold_until = $2 AND hold_until > $4' in query
            assert args == (owner.feed.id, owner.feed.hold_until, until, now)
            assert kwargs['timeout'] == 10
            return 'UPDATE 0'

    owner = lease()
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=1)
    assert not await store.renew_feed_lease(Conn(), owner.feed, now=now, until=until)
