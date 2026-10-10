"""Queue durability, fencing, outcomes and control against isolated PostgreSQL."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from xjobscore import probe, reconcile, run, store
from xjobscore.net import NetError, Response
from xjobscore.tests.test_closure_postgres import _seed
from xjobscore.tests.test_missing_since_postgres import board, listing


class Net:
    def __init__(self, outcome='live'):
        self.outcome = outcome
        self.calls = []
    async def get(self, url, **kwargs):
        self.calls.append(url)
        if self.outcome in {'dead','blocked'}:
            raise NetError('HTTP error',url=url,status=404 if self.outcome=='dead' else 403)
        body = b'This job has expired' if self.outcome=='closed' else b'Engineer Apply'
        return Response(url='https://jobs.example/careers' if self.outcome=='redirect' else url,
                        status=200,headers={},body=body)


@pytest.fixture
async def jobs(core_schema):
    pool=core_schema.pool
    async with pool.acquire() as conn:
        await _seed(conn,['one','two','three'])
    return pool


async def take(pool, limit=1):
    async with pool.acquire() as conn:
        return await reconcile.claim(conn,limit),await reconcile.control(conn)


async def test_concurrent_enqueue_and_claim_are_unique(jobs):
    await jobs.execute('DELETE FROM reconcile_queue')
    async def enqueue():
        async with jobs.acquire() as conn:
            return await reconcile.enqueue(conn)
    assert sum(await asyncio.gather(enqueue(),enqueue(),enqueue()))==3
    async def claim():
        async with jobs.acquire() as conn:
            return await reconcile.claim(conn,2)
    a,b=await asyncio.gather(claim(),claim())
    assert len(a)+len(b)==3
    assert len({(x['posting_id'],x['missing_since']) for x in a+b})==3


async def test_missing_and_enqueue_roll_back_together(jobs,monkeypatch):
    await jobs.execute('DELETE FROM reconcile_queue; UPDATE job_posting SET missing_since=NULL')
    async def fail(*args,**kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(reconcile,'enqueue',fail)
    async with jobs.acquire() as conn:
        with pytest.raises(RuntimeError):
            await store.mark_missing_postings(conn,'lever:closure-test',reconcile.now())
    assert await jobs.fetchval('SELECT count(*) FROM job_posting WHERE missing_since IS NOT NULL')==0


@pytest.mark.parametrize('outcome,column',[('dead','linked_dead_at'),('closed','paged_closed_at'),('redirect','paged_closed_at')])
async def test_confirmed_closure_is_atomic_and_preserves_content(jobs,outcome,column):
    claimed,settings=await take(jobs)
    job=claimed[0]
    before=await jobs.fetchrow('SELECT * FROM job_posting WHERE id=$1',job['posting_id'])
    await reconcile.Worker(None,Net(outcome))._check(jobs,job,settings)
    after=await jobs.fetchrow('SELECT * FROM job_posting WHERE id=$1',job['posting_id'])
    assert after['status']=='closed' and after[column] is not None
    for field in ('title','description','last_seen_at','content_hash','raw_id','missing_since','ats_closed_at'):
        assert after[field]==before[field]
    assert await jobs.fetchval('SELECT finished_at IS NOT NULL FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])
    assert await jobs.fetchval("SELECT count(*) FROM raw_posting WHERE data->'payload' ? '_probe'")==1
    # Replaying a finished claim cannot write evidence again.
    async with jobs.acquire() as conn:
        await reconcile.finish(conn,job,probe.Probe('absent',url=before['source_url']),settings)
    assert await jobs.fetchval("SELECT count(*) FROM raw_posting WHERE data->'payload' ? '_probe'")==1


async def test_database_failure_rolls_back_evidence_and_ack(jobs,monkeypatch):
    claimed,settings=await take(jobs)
    job=claimed[0]
    async def fail(*args,**kwargs): raise RuntimeError('write failed')
    monkeypatch.setattr(store,'close_posting_by_key',fail)
    url=await jobs.fetchval('SELECT source_url FROM job_posting WHERE id=$1',job['posting_id'])
    async with jobs.acquire() as conn:
        with pytest.raises(RuntimeError):
            await reconcile.finish(conn,job,probe.Probe('absent',url=url),settings)
    assert await jobs.fetchval("SELECT count(*) FROM raw_posting WHERE data->'payload' ? '_probe'")==0
    assert await jobs.fetchval('SELECT link_checked_at FROM job_posting WHERE id=$1',job['posting_id']) is None
    assert await jobs.fetchval('SELECT lease_token FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])==job['lease_token']


@pytest.mark.parametrize('outcome',['live','blocked'])
async def test_rechecks_and_backoff_reuse_entry(jobs,outcome):
    claimed,settings=await take(jobs)
    job=claimed[0]
    net=Net(outcome)
    worker=reconcile.Worker(None,net)
    for failure in range(1,4):
        if failure>1:
            await jobs.execute('UPDATE reconcile_queue SET next_check_at=now() WHERE posting_id=$1',job['posting_id'])
            async with jobs.acquire() as conn:
                job=(await reconcile.claim(conn,3))
                job=next(x for x in job if x['posting_id']==claimed[0]['posting_id'])
        await worker._check(jobs,job,settings)
        row=await jobs.fetchrow('SELECT * FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])
        delay=(row['next_check_at']-row['last_checked_at']).total_seconds()
        assert delay==(settings['recheck_s'] if outcome=='live' else settings['retry_s']*2**(failure-1))
        assert row['finished_at'] is None
        assert row['failures']==(0 if outcome=='live' else failure)
    assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue')==3


async def test_expired_lease_recovers_and_old_owner_is_fenced(jobs):
    claimed,settings=await take(jobs,3)
    old=claimed[0]
    await jobs.execute('UPDATE reconcile_queue SET lease_until=now()-interval \'1 second\' WHERE posting_id=$1',old['posting_id'])
    fresh,_=await take(jobs)
    assert fresh[0]['posting_id']==old['posting_id'] and fresh[0]['lease_token']!=old['lease_token']
    net=Net('dead')
    await reconcile.Worker(None,net)._check(jobs,old,settings)
    assert net.calls==[]
    await reconcile.Worker(None,net)._check(jobs,fresh[0],settings)
    assert len(net.calls)==1


async def test_reappearance_before_and_during_check_discards_result(jobs):
    claimed,settings=await take(jobs,3)
    first,second=claimed[:2]
    await jobs.execute('UPDATE job_posting SET missing_since=NULL WHERE id=$1',first['posting_id'])
    net=Net('dead')
    await reconcile.Worker(None,net)._check(jobs,first,settings)
    assert not net.calls
    class ReturnDuringCheck(Net):
        async def get(self,url,**kwargs):
            await jobs.execute('UPDATE job_posting SET missing_since=NULL WHERE id=$1',second['posting_id'])
            return await super().get(url,**kwargs)
    await reconcile.Worker(None,ReturnDuringCheck('dead'))._check(jobs,second,settings)
    assert await jobs.fetchval("SELECT count(*) FROM job_posting WHERE status='closed'")==0
    assert await jobs.fetchval("SELECT count(*) FROM reconcile_queue WHERE last_outcome='cancelled'")==2
    # A subsequent disappearance is a new episode, with a fresh entry.
    async with jobs.acquire() as conn:
        await store.mark_missing_postings(conn,'lever:closure-test',reconcile.now())
    assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue')==5


async def test_url_changes_discard_old_closure(jobs):
    claimed,settings=await take(jobs)
    job=claimed[0]
    url=await jobs.fetchval('SELECT source_url FROM job_posting WHERE id=$1',job['posting_id'])
    await jobs.execute("UPDATE job_posting SET source_url='https://jobs.example/new' WHERE id=$1",job['posting_id'])
    async with jobs.acquire() as conn:
        await reconcile.finish(conn,job,probe.Probe('absent',url=url),settings)
    assert await jobs.fetchval('SELECT finished_at FROM reconcile_queue WHERE posting_id=$1',job['posting_id']) is None
    assert await jobs.fetchval("SELECT status FROM job_posting WHERE id=$1",job['posting_id'])=='active'


async def test_no_url_waits_without_check_stamp(jobs):
    claimed,settings=await take(jobs)
    job=claimed[0]
    await jobs.execute("DELETE FROM feed; UPDATE job_posting SET source_url=NULL,apply_url=NULL")
    net=Net()
    await reconcile.Worker(None,net)._check(jobs,job,settings)
    assert not net.calls
    assert await jobs.fetchval('SELECT link_checked_at FROM job_posting WHERE id=$1',job['posting_id']) is None
    assert await jobs.fetchval('SELECT last_outcome FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])=='no_url'


async def test_settings_pause_and_reschedule(jobs):
    claimed,settings=await take(jobs)
    await reconcile.Worker(None,Net())._check(jobs,claimed[0],settings)
    async with jobs.acquire() as conn:
        await reconcile.set_control(conn,{'running':False,'recheck_s':3600,'concurrency':1})
        assert await reconcile.claim(conn,10)==[]
        row=await conn.fetchrow('SELECT * FROM reconcile_queue WHERE posting_id=$1',claimed[0]['posting_id'])
        assert (row['next_check_at']-row['last_checked_at']).total_seconds()==3600
        await reconcile.set_control(conn,{'running':True})
        assert len(await reconcile.claim(conn,10))==2


async def test_cli_preview_and_enqueue_do_not_fetch(jobs):
    await jobs.execute('DELETE FROM reconcile_queue')
    result=await run.reconcile(jobs,None,run.Config(dsn='',user_agent='test',dry_run=True))
    assert result.candidates==3 and result.queued==0
    assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue')==0
    result=await run.reconcile(jobs,None,run.Config(dsn='',user_agent='test'))
    assert result.queued==3


async def test_worker_pause_concurrency_and_shutdown(jobs):
    started=asyncio.Event()
    release=asyncio.Event()
    class SlowNet(Net):
        async def get(self,url,**kwargs):
            started.set()
            await release.wait()
            return await super().get(url,**kwargs)
    async def pool(): return jobs
    async with jobs.acquire() as conn:
        await reconcile.set_control(conn,{'concurrency':1})
    worker=reconcile.Worker(SimpleNamespace(pool_or_none=pool),SlowNet(),shutdown_grace_s=.01)
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(),2)
        async with jobs.acquire() as conn:
            await reconcile.set_control(conn,{'running':False})
        worker.nudge()
        await asyncio.sleep(.1)
        assert worker.status()['checking']==1
        assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue WHERE lease_until IS NOT NULL')==1
    finally:
        await worker.stop()
    assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue WHERE lease_until IS NOT NULL')==0


async def test_blocked_checker_does_not_block_polling(board):
    await board.poll(listing('gone','kept'),0)
    await board.poll(listing('kept'),15)
    claimed,settings=await take(board.pool)
    started=asyncio.Event();release=asyncio.Event()
    class SlowNet(Net):
        async def get(self,url,**kwargs):
            started.set();await release.wait()
            return await super().get(url,**kwargs)
    task=asyncio.create_task(reconcile.Worker(None,SlowNet())._check(board.pool,claimed[0],settings))
    try:
        await asyncio.wait_for(started.wait(),2)
        assert await asyncio.wait_for(board.poll(listing('kept'),30),2)=='ok'
        assert not task.done()
    finally:
        release.set();await task
    assert await board.pool.fetchval('SELECT count(*) FROM reconcile_queue')==1

async def test_migration_reruns_preserve_settings_queue_and_evidence(jobs,live_migration):
    migration=live_migration('20261006_link_check_queue.sql')
    async with jobs.acquire() as conn:
        await reconcile.set_control(conn,{'running':False,'concurrency':7})
        original=await conn.fetch('SELECT * FROM reconcile_queue ORDER BY posting_id')
        evidence=await conn.fetch('SELECT id,data,data_hash FROM raw_posting ORDER BY id')
        await conn.execute(migration)
        await conn.execute(migration)
        assert await reconcile.control(conn)=={**await reconcile.control(conn),'running':False,'concurrency':7}
        assert await conn.fetch('SELECT * FROM reconcile_queue ORDER BY posting_id')==original
        assert await conn.fetch('SELECT id,data,data_hash FROM raw_posting ORDER BY id')==evidence


async def test_fresh_jobs_precede_backlog(jobs):
    await jobs.execute('UPDATE reconcile_queue SET priority=1')
    posting=await jobs.fetchval('SELECT max(posting_id) FROM reconcile_queue')
    await jobs.execute('UPDATE reconcile_queue SET priority=0 WHERE posting_id=$1',posting)
    claimed,_=await take(jobs)
    assert claimed[0]['posting_id']==posting


async def test_deadline_is_inconclusive_and_live_resets_failures(jobs,monkeypatch):
    monkeypatch.setattr(reconcile,'DEADLINE_S',.01)
    class SlowNet(Net):
        async def get(self,*args,**kwargs):
            await asyncio.sleep(10)
    claimed,settings=await take(jobs,3)
    job=claimed[0]
    await reconcile.Worker(None,SlowNet())._check(jobs,job,settings)
    assert await jobs.fetchval('SELECT failures FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])==1
    await jobs.execute('UPDATE reconcile_queue SET next_check_at=now(),failures=50 WHERE posting_id=$1',job['posting_id'])
    next_jobs,_=await take(jobs)
    await reconcile.Worker(None,Net('blocked'))._check(jobs,next_jobs[0],settings)
    row=await jobs.fetchrow('SELECT * FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])
    assert (row['next_check_at']-row['last_checked_at']).total_seconds()==21600
    await jobs.execute('UPDATE reconcile_queue SET next_check_at=now() WHERE posting_id=$1',job['posting_id'])
    next_jobs,_=await take(jobs)
    await reconcile.Worker(None,Net())._check(jobs,next_jobs[0],settings)
    assert await jobs.fetchval('SELECT failures FROM reconcile_queue WHERE posting_id=$1',job['posting_id'])==0


async def test_claimed_ats_closed_job_does_not_fetch(jobs):
    claimed,settings=await take(jobs)
    await jobs.execute("UPDATE job_posting SET status='closed',ats_closed_at=now() WHERE id=$1",claimed[0]['posting_id'])
    net=Net('dead')
    await reconcile.Worker(None,net)._check(jobs,claimed[0],settings)
    assert net.calls==[]
    assert await jobs.fetchval('SELECT linked_dead_at FROM job_posting WHERE id=$1',claimed[0]['posting_id']) is None


async def test_controls_api_validates_and_persists(jobs):
    import httpx
    from xjobscore.console.test_support import create_app
    async def pool(): return jobs
    app=create_app(policy=SimpleNamespace(pool_or_none=pool),catalog=object(),store=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/reconcile/control')
        assert response.status_code==200 and response.json()['queue']['ready']==3
        response=await client.post('/api/reconcile/control',json={'running':False,'concurrency':2,'checks_per_second':.5,'recheck_s':3600})
        assert response.status_code==200
        assert response.json()['control']['running'] is False
        assert response.json()['control']['retry_s']==900
        for changes in ({'concurrency':0},{'concurrency':33},{'checks_per_second':0},{'retry_s':59},{'recheck_s':604801},{'unknown':1},{}):
            assert (await client.post('/api/reconcile/control',json=changes)).status_code==422


async def test_queue_counts_and_retention(jobs):
    claimed,settings=await take(jobs,3)
    worker=reconcile.Worker(None,Net('dead'))
    await worker._check(jobs,claimed[0],settings)
    await jobs.execute("UPDATE reconcile_queue SET finished_at=now()-interval '31 days' WHERE posting_id=$1",claimed[0]['posting_id'])
    async def pool(): return jobs
    async with jobs.acquire() as conn:
        await reconcile.set_control(conn,{'running':False})
    running=reconcile.Worker(SimpleNamespace(pool_or_none=pool),Net())
    await running.start()
    try:
        for _ in range(100):
            if await jobs.fetchval('SELECT count(*) FROM reconcile_queue')==2:
                break
            await asyncio.sleep(.01)
        assert await jobs.fetchval('SELECT count(*) FROM reconcile_queue')==2
        view=await reconcile.snapshot(jobs,running)
        assert view['worker']['alive'] and view['queue']['checking']==2
    finally:
        await running.stop()


async def test_lost_partial_and_repeated_polls_enqueue_correctly(board):
    await board.poll(listing('gone','kept'),0)
    await board.poll(listing('kept',truncated=True),15)
    assert await board.pool.fetchval('SELECT count(*) FROM reconcile_queue')==0
    await board.poll(listing('kept'),30,lose_lease=True)
    assert await board.pool.fetchval('SELECT count(*) FROM reconcile_queue')==0
    await board.poll(listing('kept'),45)
    await board.poll(listing('kept'),60)
    assert await board.pool.fetchval('SELECT count(*) FROM reconcile_queue')==1
