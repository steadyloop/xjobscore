"""Exercise independent closure signals against isolated real PostgreSQL tables."""
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from xjobscore import probe, reconcile, router, run, silver, store
from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Item

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


async def _seed(conn, keys):
    company = await conn.fetchval("INSERT INTO company(name) VALUES ('Closure test') RETURNING id")
    await conn.execute(
        "INSERT INTO token(ats, token, company_id, status) VALUES ('lever','closure-test',$1,'live')",
        company,
    )
    feed_id = await conn.fetchval("INSERT INTO feed(source,key) VALUES ('lever','closure-test') RETURNING id")
    for key in keys:
        raw = await conn.fetchval(
            "INSERT INTO raw_posting(source,source_key,data,data_hash) VALUES ('lever:closure-test',$1,'{}',$1) RETURNING id",
            key,
        )
        await conn.execute(
            """INSERT INTO job_posting(source,source_key,company_id,title,description,source_url,content_hash,raw_id,last_seen_at)
               VALUES ('lever:closure-test',$1,$2,'Original title','Original description',$3,'original-hash',$4,$5)""",
            key, company, f"https://jobs.example/{key}", raw, NOW,
        )
    await conn.execute("INSERT INTO poll_log(feed_id,polled_at,status) VALUES ($1,$2,'ok')", feed_id, NOW+timedelta(minutes=15))
    await store.mark_missing_postings(conn, "lever:closure-test", NOW+timedelta(minutes=15))
    return Feed(id=feed_id,source="lever",key="closure-test",company_id=company)


async def test_reconcile_distinguishes_dead_closed_redirected_live_and_blocked(core_schema, monkeypatch):
    keys = ["dead", "closed", "redirect", "live", "blocked"]
    async with core_schema.pool.acquire() as conn:
        feed = await _seed(conn,keys)

    async def feeds(conn): return [feed]
    monkeypatch.setattr(store,"replay_feeds",feeds)

    class Net:
        async def get(self,url,**kwargs):
            key=url.rsplit('/',1)[-1]
            if key in {"dead","blocked"}:
                raise NetError("HTTP error",url=url,status=404 if key=="dead" else 403)
            if key=="redirect":
                return Response(url="https://jobs.example/careers",status=200,headers={},body=b"Careers")
            body=b"<h1>This job has expired</h1>" if key=="closed" else b"<h1>Engineer</h1><button>Apply</button>"
            return Response(url=url,status=200,headers={},body=body)

    cfg=run.Config(dsn=core_schema.dsn,user_agent="test",concurrency=2)
    preview=await run.reconcile(core_schema.pool,None,cfg)
    assert preview.queued==0  # Already queued atomically by the complete poll.
    worker=reconcile.Worker(None,Net())
    async with core_schema.pool.acquire() as conn:
        jobs=await reconcile.claim(conn,5)
        settings=await reconcile.control(conn)
    for job in jobs:
        await worker._check(core_schema.pool,job,settings)
    async with core_schema.pool.acquire() as conn:
        rows={r['source_key']:r for r in await conn.fetch("SELECT * FROM job_posting")}
        for key,row in rows.items():
            assert row['missing_since']==NOW+timedelta(minutes=15)
            assert row['ats_closed_at'] is None
            assert row['title']=='Original title'
            assert row['description']=='Original description'
            assert row['content_hash']=='original-hash'
            assert row['last_seen_at']==NOW
            assert row['link_checked_at'] is not None
            assert (row['linked_dead_at'] is not None)==(key=='dead')
            assert (row['paged_closed_at'] is not None)==(key in {'closed','redirect'})
            assert row['status']==('closed' if key in {'dead','closed','redirect'} else 'active')

        # Re-observing one channel must preserve the first timestamp; another
        # channel can coexist, without claiming an ATS closure.
        first=rows['closed']['paged_closed_at']
        await store.close_posting_by_key(conn,source='lever:closure-test',source_key='closed',closed_at=first+timedelta(hours=1),closure_kind='page')
        await store.close_posting_by_key(conn,source='lever:closure-test',source_key='closed',closed_at=first+timedelta(hours=2))
        row=await conn.fetchrow("SELECT * FROM job_posting WHERE source_key='closed'")
        assert row['paged_closed_at']==first
        assert row['linked_dead_at']==first+timedelta(hours=2)
        assert row['ats_closed_at'] is None


async def test_migration_is_idempotent_and_keeps_existing_signal_values(core_schema, live_migration):
    migration=live_migration('20261006_job_posting_closure_signal_names.sql')
    async with core_schema.pool.acquire() as conn:
        await _seed(conn,['existing'])
        await conn.execute("UPDATE job_posting SET ats_closed_at=$1,linked_dead_at=$1,paged_closed_at=$1,link_checked_at=$1",NOW)
        # Reproduce the live schema before renaming, with evidence in each channel.
        await conn.execute("ALTER TABLE job_posting RENAME COLUMN ats_closed_at TO vendor_closed_at")
        await conn.execute("ALTER TABLE job_posting RENAME COLUMN linked_dead_at TO link_closed_at")
        before = dict(await conn.fetchrow("SELECT * FROM job_posting"))
        await conn.execute(migration)
        await conn.execute(migration)
        row=await conn.fetchrow("SELECT * FROM job_posting")
        assert row['status']=='active'
        assert row['missing_since']==NOW+timedelta(minutes=15)
        assert row['linked_dead_at']==row['ats_closed_at']==NOW
        assert row['paged_closed_at']==NOW
        before['ats_closed_at'] = before.pop('vendor_closed_at')
        before['linked_dead_at'] = before.pop('link_closed_at')
        assert dict(row) == before
        signal_columns = await conn.fetch("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='job_posting'
              AND (column_name LIKE '%closed_at' OR column_name LIKE '%dead_at'
                   OR column_name='missing_since')
        """)
        assert {r['column_name'] for r in signal_columns} == {
            'missing_since','ats_closed_at','linked_dead_at','paged_closed_at'
        }


async def test_signal_rename_refuses_conflicting_columns_without_losing_evidence(core_schema, live_migration):
    migration=live_migration('20261006_job_posting_closure_signal_names.sql')
    async with core_schema.pool.acquire() as conn:
        await _seed(conn,['conflict'])
        await conn.execute("ALTER TABLE job_posting ADD COLUMN vendor_closed_at timestamptz")
        await conn.execute("UPDATE job_posting SET vendor_closed_at=$1,ats_closed_at=$2", NOW, NOW+timedelta(hours=1))
        with pytest.raises(asyncpg.RaiseError, match='Both vendor_closed_at and ats_closed_at exist'):
            await conn.execute(migration)
        await conn.execute('ROLLBACK')
        row=await conn.fetchrow("SELECT vendor_closed_at,ats_closed_at FROM job_posting")
        assert row['vendor_closed_at']==NOW
        assert row['ats_closed_at']==NOW+timedelta(hours=1)


async def test_page_closure_replays_without_vendor_parser(core_schema, monkeypatch):
    def unexpected_parser(*args):
        raise AssertionError('probe evidence must not use the vendor parser')
    monkeypatch.setattr(silver.sources,'parser',lambda source:unexpected_parser)
    async with core_schema.pool.acquire() as conn:
        await _seed(conn,['page'])
        feed=Feed(id=0,source='lever',key='closure-test')
        fact=probe.Probe('page_closed','https://jobs.example/page','https://jobs.example/careers',200,'job page redirected')
        payload=probe.tombstone(fact,probed_at=NOW)
        stored=[router.StoredRaw(Item('page',payload),0,'probe',True)]
        counts=await silver.process(conn,feed,stored,{},seen_at=NOW+timedelta(days=1),touch_seen=False)
        assert counts.postings==1
        row=await conn.fetchrow("SELECT * FROM job_posting WHERE source_key='page'")
        assert row['paged_closed_at']==NOW
        assert row['linked_dead_at'] is None
        assert row['ats_closed_at'] is None
        assert row['title']=='Original title'
