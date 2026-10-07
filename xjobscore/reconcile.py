"""Durable, independently controlled checks of missing postings."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from urllib.parse import urlsplit
from uuid import uuid4

from xjobscore import probe, router, sources, store

logger = logging.getLogger(__name__)
IDLE_S = 5
DEADLINE_S = 120
LEASE_S = 300
RETENTION_DAYS = 30


def now():
    return datetime.now(timezone.utc)


async def enqueue(conn, posting_ids=None, *, priority=0, dry_run=False):
    """Queue each active missing episode once; an empty ID list means no work."""
    if posting_ids == []:
        return 0
    args = [] if posting_ids is None else [posting_ids]
    scope = "" if posting_ids is None else "AND j.id = ANY($1::bigint[])"
    select = f"""SELECT j.id, j.missing_since FROM job_posting j
        WHERE j.status = 'active' AND j.missing_since IS NOT NULL {scope}
        AND NOT EXISTS (SELECT 1 FROM reconcile_queue q
            WHERE q.posting_id=j.id AND q.missing_since=j.missing_since)"""
    if dry_run:
        return await conn.fetchval(f"SELECT count(*) FROM ({select}) eligible", *args)
    return await conn.fetchval(f"""WITH inserted AS (
        INSERT INTO reconcile_queue(posting_id,missing_since,priority)
        SELECT eligible.*, ${len(args)+1}::smallint FROM ({select}) eligible
        ON CONFLICT (posting_id,missing_since) DO NOTHING RETURNING 1
    ) SELECT count(*) FROM inserted""", *args, priority)


async def control(conn):
    return dict(await conn.fetchrow("SELECT * FROM reconcile_control WHERE id"))


async def set_control(conn, changes):
    allowed = {"running", "concurrency", "checks_per_second", "recheck_s", "retry_s"}
    if not changes or changes.keys() - allowed:
        raise ValueError("unknown or empty reconciliation settings")
    async with conn.transaction():
        old = await conn.fetchrow("SELECT * FROM reconcile_control WHERE id FOR UPDATE")
        values = {**dict(old), **changes}
        if not (1 <= values['concurrency'] <= 32 and .1 <= values['checks_per_second'] <= 20
                and 60 <= values['recheck_s'] <= 604800 and 60 <= values['retry_s'] <= 604800):
            raise ValueError("invalid reconciliation settings")
        await conn.execute("""UPDATE reconcile_control SET running=$1, concurrency=$2,
            checks_per_second=$3,recheck_s=$4,retry_s=$5,changed_at=now() WHERE id""",
            *(values[k] for k in ("running","concurrency","checks_per_second","recheck_s","retry_s")))
        if any(values[k] != old[k] for k in ("recheck_s", "retry_s")):
            await conn.execute("""UPDATE reconcile_queue SET next_check_at=GREATEST(now(),
                last_checked_at + make_interval(secs => CASE WHEN last_outcome='inconclusive'
                    THEN LEAST($2 * power(2, LEAST(GREATEST(failures-1,0),16)), GREATEST($1,$2))
                    ELSE $1 END))
                WHERE finished_at IS NULL AND last_checked_at IS NOT NULL
                  AND (lease_until IS NULL OR lease_until <= now())""",
                values['recheck_s'], values['retry_s'])
    return await control(conn)


async def snapshot(pool, worker=None):
    async with pool.acquire() as conn:
        settings = await control(conn)
        row = await conn.fetchrow("""SELECT
            count(*) FILTER(WHERE finished_at IS NULL AND (lease_until IS NULL OR lease_until<=now()) AND next_check_at<=now()) AS ready,
            count(*) FILTER(WHERE finished_at IS NULL AND lease_until>now()) AS checking,
            count(*) FILTER(WHERE finished_at IS NULL AND (lease_until IS NULL OR lease_until<=now()) AND next_check_at>now()) AS waiting,
            count(*) FILTER(WHERE finished_at IS NOT NULL AND last_outcome<>'cancelled') AS completed,
            count(*) FILTER(WHERE last_outcome='cancelled') AS cancelled,
            (SELECT last_error FROM reconcile_queue WHERE last_error IS NOT NULL ORDER BY last_checked_at DESC LIMIT 1) AS last_error,
            min(next_check_at) FILTER(WHERE finished_at IS NULL AND next_check_at<=now()
                AND (lease_until IS NULL OR lease_until<=now())) AS oldest_ready_at
            FROM reconcile_queue""")
    return {"control": {k: v.isoformat() if isinstance(v,datetime) else v for k,v in settings.items()},
            "queue": {k: v.isoformat() if isinstance(v,datetime) else v for k,v in dict(row).items()},
            "worker": worker.status() if worker else {"alive": False, "last_error": None}}


async def claim(conn, limit=1):
    return await conn.fetch("""WITH picked AS (
        SELECT posting_id,missing_since FROM reconcile_queue
        WHERE finished_at IS NULL AND next_check_at<=now()
          AND (lease_until IS NULL OR lease_until<=now())
          AND (SELECT running FROM reconcile_control WHERE id)
        ORDER BY priority,next_check_at,posting_id LIMIT $1 FOR UPDATE SKIP LOCKED
    ) UPDATE reconcile_queue q SET lease_until=now()+make_interval(secs=>$3),lease_token=$2
      FROM picked p WHERE q.posting_id=p.posting_id AND q.missing_since=p.missing_since
      RETURNING q.*""", limit, uuid4(), LEASE_S)


async def release(conn, job, *, delay=0):
    await conn.execute("""UPDATE reconcile_queue SET lease_token=NULL,lease_until=NULL,
        next_check_at=GREATEST(next_check_at,now()+make_interval(secs=>$4))
        WHERE posting_id=$1 AND missing_since=$2 AND lease_token=$3""",
        job['posting_id'],job['missing_since'],job['lease_token'],delay)


def probe_url(feed):
    """Use the adapter's existing per-posting API fallback."""
    try:
        adapter = sources.json_api.get_adapter(feed.source)
    except KeyError:
        return None
    template = (adapter.get("probe") or {}).get("url")
    if not template:
        return None
    parts = adapter.get("key_parts", ())
    context = {"key": feed.key}
    if parts:
        context.update(zip(parts, feed.key.split("/", len(parts)-1)))
    context.update(feed.config or {})
    for token,value in context.items():
        if isinstance(value,str):
            template = template.replace("{"+token+"}",value)
    return template


def valid_url(url):
    try:
        return bool(url and urlsplit(url).scheme in {'http','https'} and urlsplit(url).netloc)
    except ValueError:
        return False


async def target(conn, posting):
    for url in (posting['source_url'],posting['apply_url']):
        url = (url or '').strip()
        if valid_url(url):
            return url, True
    vendor,key = posting['source'].split(':',1)
    row = await conn.fetchrow(store._FEED_SELECT+" WHERE f.source=$1 AND f.key=$2",vendor,key)
    url = probe_url(store._feed_from_row(row)) if row else None
    url = url.replace('{id}',posting['source_key']) if url else None
    return (url,False) if valid_url(url) else (None,False)


def current(posting, job):
    return posting and posting['status']=='active' and posting['missing_since']==job['missing_since']


async def finish(conn, job, result, settings, *, checked_at=None):
    """Fence stale workers and commit evidence, closure and acknowledgment together."""
    async with conn.transaction():
        posting = await conn.fetchrow("SELECT id,source,source_key,status,missing_since,source_url,apply_url FROM job_posting WHERE id=$1 FOR UPDATE",job['posting_id'])
        queued = await conn.fetchrow("""SELECT * FROM reconcile_queue
            WHERE posting_id=$1 AND missing_since=$2 FOR UPDATE""",job['posting_id'],job['missing_since'])
        if not queued or queued['lease_token']!=job['lease_token'] or queued['lease_until']<=now():
            return
        cancelled = not current(posting,job)
        # Re-resolve URLs too: a changed target invalidates the old response.
        if not cancelled and result is not None:
            url,_ = await target(conn,posting)
            if url != result.url:
                cancelled = True
                # Same episode, new URL: retry it rather than finish the episode.
                await release(conn,job)
                return
        outcome = 'cancelled' if cancelled else result.outcome if result else 'no_url'
        completed = cancelled or outcome in {'absent','page_closed'}
        failures = queued['failures']+1 if outcome=='inconclusive' else 0
        delay = min(settings['retry_s'] * 2**min(failures-1,16),max(settings['retry_s'],settings['recheck_s'])) if failures else settings['recheck_s']
        stamp = checked_at or now()
        if not cancelled and result is not None:
            await store.mark_checked(conn,[posting['id']],checked_at=stamp)
            if completed:
                data = router.wrap(probe.tombstone(result,probed_at=stamp))
                await store.save_raw(conn,source=posting['source'],source_key=posting['source_key'],data=data,data_hash=router.encoded_hash(data))
                await store.close_posting_by_key(conn,source=posting['source'],source_key=posting['source_key'],closed_at=stamp,
                    closure_kind='page' if outcome=='page_closed' else 'link')
        await conn.execute("""UPDATE reconcile_queue SET lease_until=NULL,lease_token=NULL,
            priority=1,last_outcome=$4,last_error=$5,last_checked_at=$6,failures=$7,
            next_check_at=$6::timestamptz+make_interval(secs=>$8),finished_at=CASE WHEN $9 THEN $6 ELSE NULL END
            WHERE posting_id=$1 AND missing_since=$2 AND lease_token=$3""",
            job['posting_id'],job['missing_since'],job['lease_token'],outcome,
            result.detail if result and outcome=='inconclusive' else None,stamp,failures,delay,completed)


class Worker:
    """Drain free slots continuously, independently of feed scheduling."""
    def __init__(self, policy, net, *, shutdown_grace_s=30, ownership_check=None):
        self.policy,self.net = policy,net
        self.grace,self.ownership_check = shutdown_grace_s,ownership_check
        self._task = None
        self._active = set()
        self._wake = asyncio.Event()
        self._stopping = False
        self._last_error = None
        self._next_start = 0.
        self._pruned_at = -86400.

    def status(self):
        return {'alive': self._task is not None and not self._task.done(),
                'checking':len(self._active),'last_error':self._last_error}

    def nudge(self):
        self._wake.set()

    async def start(self):
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._loop(),name='xjobscore-reconcile')

    async def stop(self):
        self._stopping = True
        self.nudge()
        if self._task:
            await self._task
        if self._active:
            _,pending = await asyncio.wait(self._active,timeout=self.grace)
            for task in pending:
                task.cancel()
            await asyncio.gather(*self._active,return_exceptions=True)
        self._task = None

    def _done(self, task):
        self._active.discard(task)
        if not task.cancelled() and task.exception():
            self._last_error = str(task.exception())
            logger.error('link check failed: %s',task.exception())
        self.nudge()

    async def _check(self, pool, job, settings):
        delay = 0
        try:
            async with pool.acquire() as conn:
                posting = await conn.fetchrow("""SELECT j.id,j.source,j.source_key,j.status,j.missing_since,j.source_url,j.apply_url
                    FROM job_posting j JOIN reconcile_queue q ON q.posting_id=j.id
                    WHERE j.id=$1 AND q.missing_since=$2 AND q.lease_token=$3 AND q.lease_until>now()""",
                    job['posting_id'],job['missing_since'],job['lease_token'])
                url,is_page = await target(conn,posting) if current(posting,job) else (None,False)
            result = None
            if url:
                try:
                    async with asyncio.timeout(DEADLINE_S):
                        result = await probe.probe(self.net,url,concurrency=settings['concurrency'],page=is_page)
                except Exception as exc:
                    result = probe.Probe('inconclusive',url=url,detail=f'{type(exc).__name__}: {exc}')
            async with pool.acquire() as conn:
                await finish(conn,job,result,settings)
        except Exception:
            delay = settings['retry_s']
            raise
        finally:
            try:
                async with pool.acquire() as conn:
                    await release(conn,job,delay=delay)
            except Exception:
                logger.exception('could not release link-check claim; lease will expire')

    async def _loop(self):
        while not self._stopping:
            delay = IDLE_S
            try:
                if self.ownership_check:
                    await self.ownership_check()
                pool = await self.policy.pool_or_none()
                if pool is None:
                    raise RuntimeError('PostgreSQL unavailable')
                async with pool.acquire() as conn:
                    settings = await control(conn)
                    if time.monotonic()-self._pruned_at>=86400:
                        await conn.execute('DELETE FROM reconcile_queue WHERE finished_at < now()-make_interval(days=>$1)',RETENTION_DAYS)
                        self._pruned_at = time.monotonic()
                    if settings['running'] and len(self._active)<settings['concurrency']:
                        delay = max(0,self._next_start-time.monotonic())
                        if delay<=0:
                            jobs = await claim(conn)
                            if jobs:
                                task = asyncio.create_task(self._check(pool,jobs[0],settings))
                                self._active.add(task)
                                task.add_done_callback(self._done)
                                self._next_start = time.monotonic()+1/settings['checks_per_second']
                                continue
                            delay = IDLE_S
                self._last_error = None
            except Exception as exc:
                self._last_error = f'{type(exc).__name__}: {exc}'
                logger.exception('link-check queue failed')
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(),timeout=min(IDLE_S,delay))
            except asyncio.TimeoutError:
                pass
