"""Administrator edits survive publication, retries, and identity changes."""

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from xjobscore import company_edits, publish, serving
from xjobscore.tests.test_publish_postgres import FRONTEND_SQL, thin_target  # noqa: F401
from xjobscore.tests.test_serving_postgres import seed_existing

pytestmark = pytest.mark.skipif(not os.environ.get("XJOBSCORE_TEST_DSN"), reason="local PostgreSQL test DSN required")


async def queue(target, changes):
    revision = uuid4()
    await target.execute("""
        INSERT INTO company_edit(company_id,changes,edited_by,revision)
        VALUES(9,$1,$2,$3) ON CONFLICT(company_id) DO UPDATE
        SET changes=company_edit.changes || EXCLUDED.changes,
            revision=EXCLUDED.revision, sync_error=NULL
    """, changes, uuid4(), revision)
    return revision


async def send(source, target, revisions):
    mapped = serving.Source(source, await serving.prepare(source))
    await serving.validate_target(mapped, target)
    return await publish.publish(mapped, target, company_edit_revisions=revisions)


@pytest.mark.parametrize("domain", ["renamed.example", None])
async def test_edits_reach_source_and_preserve_ids_on_repeated_publications(core_schema, thin_target, domain):
    await seed_existing(core_schema, thin_target)
    revision = await queue(thin_target, {"name": "Edited company", "domain": domain, "description": None, "tags": {"startup": True}})
    async with core_schema.pool.acquire() as source:
        revisions = await company_edits.sync(source, thin_target)
        assert revisions == {9: revision}
        assert await company_edits.sync(source, thin_target) == revisions
        row = await source.fetchrow("SELECT * FROM company WHERE id=1")
        assert row['name'] == 'Edited company'
        assert row['domain'] == domain
        assert row['tags'] == {'startup': True}
        assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 1
        await send(source, thin_target, revisions)
        assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 0
        await send(source, thin_target, {})
        assert await source.fetchval("SELECT count(*) FROM frontend_identity WHERE kind='company'") == 1
    row = await thin_target.fetchrow("SELECT * FROM company WHERE id=9")
    assert row['name'] == 'Edited company'
    assert row['domain'] == domain
    assert await thin_target.fetchval("SELECT company_id FROM job_posting WHERE id=77") == 9
    assert await thin_target.fetchval("SELECT note FROM job_application WHERE job_posting_id=77") == 'Keep this note'


async def test_source_conflict_rolls_back_all_fields_and_keeps_edit_for_correction(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await core_schema.pool.execute("INSERT INTO company(name,domain) VALUES('Other','occupied.example')")
    await queue(thin_target, {"name": "Must roll back", "domain": "occupied.example"})
    async with core_schema.pool.acquire() as source:
        assert await company_edits.sync(source, thin_target) == {}
        assert await source.fetchval("SELECT name FROM company WHERE id=1") == 'Example'
        assert 'already in use' in await thin_target.fetchval("SELECT sync_error FROM company_edit")
        await send(source, thin_target, {})
        assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 1
        await queue(thin_target, {"domain": "corrected.example"})
        await send(source, thin_target, await company_edits.sync(source, thin_target))
        assert await source.fetchval("SELECT domain FROM company WHERE id=1") == 'corrected.example'
    assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 0


async def test_dry_run_leaves_source_target_and_queue_unchanged(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await queue(thin_target, {"name": "Dry run"})
    async with core_schema.pool.acquire() as source:
        assert await company_edits.sync(source, thin_target, dry_run=True) == {}
        assert await source.fetchval("SELECT name FROM company WHERE id=1") == 'Example'
    assert await thin_target.fetchval("SELECT name FROM company WHERE id=9") == 'Example'
    assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 1


async def test_newer_revision_is_not_acknowledged_by_older_publication(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await queue(thin_target, {"name": "First"})
    async with core_schema.pool.acquire() as source:
        older = await company_edits.sync(source, thin_target)
        newer = await queue(thin_target, {"description": "Second edit"})
        await send(source, thin_target, older)
        assert await thin_target.fetchval("SELECT revision FROM company_edit") == newer
        await send(source, thin_target, await company_edits.sync(source, thin_target))
        assert await source.fetchval("SELECT description FROM company WHERE id=1") == 'Second edit'
    assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 0


async def test_failed_publication_retains_queue_and_retry_keeps_source_edit(core_schema, thin_target, monkeypatch):
    await seed_existing(core_schema, thin_target)
    await queue(thin_target, {"domain": "retry.example"})
    original = publish._assert_thin_target
    async def fail(_):
        raise RuntimeError('Simulated publish outage')
    async with core_schema.pool.acquire() as source:
        revisions = await company_edits.sync(source, thin_target)
        monkeypatch.setattr(publish, '_assert_thin_target', fail)
        with pytest.raises(RuntimeError, match='Simulated publish outage'):
            await send(source, thin_target, revisions)
        assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 1
        assert await source.fetchval("SELECT domain FROM company WHERE id=1") == 'retry.example'
        monkeypatch.setattr(publish, '_assert_thin_target', original)
        await send(source, thin_target, await company_edits.sync(source, thin_target))
    assert await thin_target.fetchval("SELECT domain FROM company WHERE id=9") == 'retry.example'
    assert await thin_target.fetchval("SELECT count(*) FROM company_edit") == 0


async def test_publication_imports_local_edits_and_keeps_both_targets_current(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await core_schema.pool.execute("""
        INSERT INTO company(name,domain) VALUES('Old company','old.example');
        INSERT INTO frontend_identity(kind,key,id) VALUES('company','["domain","old.example"]',90);
    """)
    await thin_target.execute("INSERT INTO company(id,name,domain) OVERRIDING SYSTEM VALUE VALUES(90,'Old company','old.example')")
    schema = core_schema.schema + '_editor_local'
    setup = await publish.connect_target(core_schema.dsn)
    local = None
    try:
        await setup.execute('CREATE SCHEMA ' + schema)
        await setup.execute('SET search_path TO ' + schema)
        await setup.execute(FRONTEND_SQL.read_text())
        local = await publish.connect_target(core_schema.dsn, server_settings={'search_path': schema})
        await local.execute("INSERT INTO company(id,name,domain) OVERRIDING SYSTEM VALUE VALUES(9,'Example','example.com'),(90,'Old company','old.example')")
        await queue(local, {'domain': 'both.example', 'name': 'Both targets'})
        await local.execute("INSERT INTO company_edit(company_id,changes,edited_by,revision) VALUES(90,$1,$2,$3)", {'description': 'Company outside the active cohort'}, uuid4(), uuid4())

        def dsn_for(name):
            parts = urlsplit(core_schema.dsn)
            options = dict(parse_qsl(parts.query)); options['search_path'] = name
            return urlunsplit(parts._replace(query=urlencode(options)))

        targets = {'local': dsn_for(schema), 'neon': dsn_for(await thin_target.fetchval('SELECT current_schema()'))}
        for _ in range(2):
            results = await publish.publish_targets(core_schema.pool, targets)
            assert all(isinstance(result, publish.PublishCounts) for result in results.values()), results
            for target in (local, thin_target):
                assert await target.fetchval('SELECT domain FROM company WHERE id=9') == 'both.example'
                assert await target.fetchval('SELECT description FROM company WHERE id=90') == 'Company outside the active cohort'
        assert await local.fetchval('SELECT count(*) FROM company_edit') == 0
        assert await thin_target.fetchval('SELECT note FROM job_application WHERE job_posting_id=77') == 'Keep this note'
    finally:
        if local is not None:
            await local.close()
        await setup.execute('SET search_path TO public')
        await setup.execute('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE')
        await setup.close()


async def test_unrelated_company_identity_is_still_rejected(core_schema, thin_target):
    await seed_existing(core_schema, thin_target)
    await thin_target.execute("UPDATE company SET domain='unrelated.example' WHERE id=9")
    async with core_schema.pool.acquire() as source:
        mapped = serving.Source(source, await serving.prepare(source))
        with pytest.raises(RuntimeError, match='different companies'):
            await serving.validate_target(mapped, thin_target)
