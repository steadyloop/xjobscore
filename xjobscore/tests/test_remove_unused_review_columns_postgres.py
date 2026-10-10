"""Dropping unused review fields preserves every other posting field."""

from datetime import datetime, timedelta, timezone

async def test_remove_unused_review_columns_is_idempotent_and_preserves_other_data(
    core_schema, live_migration,
):
    migration = live_migration('20261006_job_posting_remove_unused_review_columns.sql')
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    async with core_schema.pool.acquire() as conn:
        # Model the existing database before this migration, including its index.
        await conn.execute('''
            ALTER TABLE job_posting
                ADD COLUMN link_marked_at timestamptz,
                ADD COLUMN removal_marked_at timestamptz;
            CREATE INDEX unused_review_removal_idx ON job_posting(removal_marked_at);
        ''')
        company = await conn.fetchval(
            "INSERT INTO company(name) VALUES ('Review cleanup test') RETURNING id"
        )
        raw = await conn.fetchval('''
            INSERT INTO raw_posting(source,source_key,data,data_hash)
            VALUES ('cleanup-test','1','{}','hash') RETURNING id
        ''')
        await conn.execute('''
            INSERT INTO job_posting(
                source,source_key,company_id,title,description,content_hash,raw_id,
                missing_since,ats_closed_at,linked_dead_at,paged_closed_at,
                link_checked_at,link_marked_at,removal_marked_at
            ) VALUES ('cleanup-test','1',$1,'Engineer','Original text','hash',$2,
                      $3,$4,$5,$6,$7,$3,$3)
        ''', company, raw, now, now+timedelta(minutes=1), now+timedelta(minutes=2),
            now+timedelta(minutes=3), now+timedelta(minutes=4))
        before = dict(await conn.fetchrow('SELECT * FROM job_posting'))
        await conn.execute(migration)
        await conn.execute(migration)
        after = dict(await conn.fetchrow('SELECT * FROM job_posting'))
        before.pop('link_marked_at')
        before.pop('removal_marked_at')
        assert after == before
        assert await conn.fetchval("SELECT to_regclass('unused_review_removal_idx')") is None
        assert not await conn.fetchval('''
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name='job_posting'
                  AND column_name IN ('link_marked_at','removal_marked_at')
            )
        ''')
