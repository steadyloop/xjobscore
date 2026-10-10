"""Serving identity survives different pipeline/Neon IDs and target failures."""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import asyncpg

from xjobscore import publish, serving
from xjobscore.tests.test_publish_postgres import FRONTEND_SQL, _seed, thin_target  # noqa: F401


pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"), reason="local PostgreSQL test DSN required"
)


async def seed_existing(core_schema, target):
    await _seed(core_schema.pool)
    await target.execute("""
        INSERT INTO company(id,name,domain) OVERRIDING SYSTEM VALUE
        VALUES(9,'Example','example.com');
        INSERT INTO job_posting(id,source,source_key,company_id,title,content_hash,job_type)
        VALUES(1,'other','old',9,'Legacy job','old','intern'),
              (77,'test','job-1',9,'Previous title','old','intern');
        INSERT INTO job_application(account_id,job_posting_id,company_snapshot,title_snapshot,note)
        VALUES('00000000-0000-0000-0000-000000000001',77,'Example','Previous title','Keep this note'),
              ('00000000-0000-0000-0000-000000000001',1,'Example','Legacy job','Keep legacy history');
    """)
    async with core_schema.pool.acquire() as source:
        await source.execute("""
            INSERT INTO frontend_identity(kind,key,id) VALUES
                ('company','["domain","example.com"]',9),
                ('job','["test","job-1"]',77),
                ('job','["other","old"]',1),
                ('meta','["initialized"]',0);
            SELECT setval(pg_get_serial_sequence('frontend_identity','id'),77,true);
        """)


async def test_preserves_neon_identity_and_application_links(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    async with core_schema.pool.acquire() as source:
        cohort = await serving.prepare(source)
        mapped = serving.Source(source, cohort)
        await serving.validate_target(mapped, thin_target)
        first = await publish.publish(mapped, thin_target)
        second = await publish.publish(mapped, thin_target)
        assert first.postings == 1
        assert second.postings == 0
        assert await thin_target.fetchval("SELECT id FROM job_posting WHERE source='test'") == 77
        assert await thin_target.fetchval("SELECT source FROM job_posting WHERE id=1") == 'other'
        assert await thin_target.fetchval("SELECT company_id FROM job_posting WHERE id=77") == 9
        assert await thin_target.fetchval("SELECT note FROM job_application WHERE job_posting_id=77") == 'Keep this note'
        assert await source.fetchval("SELECT id FROM job_posting WHERE source='test'") == 1


async def test_refuses_a_conflicting_serving_id(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await thin_target.execute("UPDATE job_posting SET source='conflict' WHERE id=77")
    async with core_schema.pool.acquire() as source:
        mapped = serving.Source(source, await serving.prepare(source))
        with pytest.raises(RuntimeError, match='different postings'):
            await serving.validate_target(mapped, thin_target)
    assert await thin_target.fetchval("SELECT source FROM job_posting WHERE id=77") == 'conflict'


async def test_local_publish_commits_when_other_target_fails(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    schema = await thin_target.fetchval('SELECT current_schema()')
    parts = urlsplit(core_schema.dsn)
    options = dict(parse_qsl(parts.query))
    options['search_path'] = schema
    dsn = urlunsplit(parts._replace(query=urlencode(options)))
    results = await publish.publish_targets(core_schema.pool, {'local':dsn,'neon':'invalid-dsn'})
    assert isinstance(results['neon'], Exception)
    assert results['local'].postings == 1
    assert await thin_target.fetchval("SELECT title FROM job_posting WHERE id=77") == 'Software Intern'


async def test_targets_share_snapshot_and_retain_central_history(core_schema, thin_target, monkeypatch):
    await seed_existing(core_schema, thin_target)
    schema = core_schema.schema + '_local_frontend'
    setup = await asyncpg.connect(core_schema.dsn)
    local = None
    try:
        await setup.execute('CREATE SCHEMA '+schema)
        await setup.execute('SET search_path TO '+schema)
        await setup.execute(FRONTEND_SQL.read_text())
        local = await publish.connect_target(core_schema.dsn, server_settings={'search_path':schema})

        def dsn_for(name):
            parts=urlsplit(core_schema.dsn)
            options=dict(parse_qsl(parts.query)); options['search_path']=name
            return urlunsplit(parts._replace(query=urlencode(options)))

        targets={'local':dsn_for(schema),'neon':dsn_for(await thin_target.fetchval('SELECT current_schema()'))}
        original = publish.publish_to

        async def change_during_publish(source, dsn, **kwargs):
            if dsn == targets['local']:
                await core_schema.pool.execute("UPDATE job_posting SET title='Changed during sync' WHERE source='test'")
            return await original(source,dsn,**kwargs)

        monkeypatch.setattr(publish,'publish_to',change_during_publish)
        results=await publish.publish_targets(core_schema.pool,targets)
        assert all(isinstance(r,publish.PublishCounts) for r in results.values())
        for target in (local,thin_target):
            assert await target.fetchval('SELECT title FROM job_posting WHERE id=77')=='Software Intern'
            assert await target.fetchval('SELECT status FROM job_posting WHERE id=1')=='closed'
        assert await local.fetchval('SELECT count(*) FROM job_application')==0
        assert await thin_target.fetchval('SELECT count(*) FROM job_application')==2
        assert await thin_target.fetchval('SELECT note FROM job_application WHERE job_posting_id=77')=='Keep this note'
        monkeypatch.setattr(publish,'publish_to',original)
        await publish.publish_targets(core_schema.pool,targets)
        for target in (local,thin_target):
            assert await target.fetchval('SELECT title FROM job_posting WHERE id=77')=='Changed during sync'
    finally:
        if local is not None:
            await local.close()
        await setup.execute('SET search_path TO public')
        await setup.execute('DROP SCHEMA IF EXISTS '+schema+' CASCADE')
        await setup.close()
