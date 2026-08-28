"""Round-trip tests for the seed export/import.

The pure-Python checks run everywhere; the round-trip needs a real database
because company identity remapping is the whole point and it is expressed in
SQL. `core_schema` (conftest.py) supplies a throwaway namespace so these never
touch a developer's real tokens.
"""

from __future__ import annotations

import json
import os

import pytest

from xjobscore import backup


# -- pure, no database ------------------------------------------------------

def test_company_key_prefers_domain_and_namespaces_the_fallback():
    assert backup.company_key("Ford", "ford.com") == "ford.com"
    assert backup.company_key("Ford", None) == "name:ford"
    # A company literally named for a domain must not collide with that domain.
    assert backup.company_key("ford.com", None) != backup.company_key("Ford", "ford.com")


def test_company_key_folds_case_and_whitespace():
    assert backup.company_key("  Acme Corp  ", None) == backup.company_key("acme corp", None)


def test_read_seed_rejects_a_file_with_no_manifest(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text('{"_": "company", "name": "Acme", "domain": null}\n')
    with pytest.raises(ValueError, match="no manifest"):
        backup.read_seed(path)


def test_read_seed_rejects_a_future_format_version(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text(json.dumps({"_": "manifest", "version": 99}) + "\n")
    with pytest.raises(ValueError, match="format version"):
        backup.read_seed(path)


def test_read_seed_reports_the_line_number_of_bad_json(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text(
        json.dumps({"_": "manifest", "version": backup.FORMAT_VERSION}) + "\n"
        + '{"_": "company", broken\n'
    )
    with pytest.raises(ValueError, match=r"seed\.jsonl:2"):
        backup.read_seed(path)


def test_read_seed_rejects_an_unknown_record_type(tmp_path):
    path = tmp_path / "seed.jsonl"
    path.write_text(
        json.dumps({"_": "manifest", "version": backup.FORMAT_VERSION}) + "\n"
        + json.dumps({"_": "posting", "id": 1}) + "\n"
    )
    with pytest.raises(ValueError, match="unknown record type"):
        backup.read_seed(path)


# -- against PostgreSQL -----------------------------------------------------

pgmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


async def _seed(pool):
    """Two companies -- one with a domain, one without -- plus aliases/tokens."""
    async with pool.acquire() as conn:
        ford = await conn.fetchval(
            "INSERT INTO company (name, domain, description) VALUES "
            "('Ford', 'ford.com', 'cars') RETURNING id"
        )
        secret = await conn.fetchval(
            "INSERT INTO company (name) VALUES ('Stealth Co') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO company_alias (alias_norm, alias, company_id) VALUES "
            "('ford', 'Ford', $1), ('stealth', 'Stealth', $2)", ford, secret,
        )
        await conn.execute(
            """
            INSERT INTO token (ats, token, company_id, status, muted, note, tags, config)
            VALUES ('eightfold', 'ford', $1, 'live', false, 'keep', ARRAY['a'],
                    '{"host": "ford.eightfold.ai"}'::jsonb),
                   ('comeet', '26.004', $2, 'live', false, NULL, NULL,
                    '{"token": "SECRET"}'::jsonb),
                   ('greenhouse', 'orphan', NULL, 'unknown', false, NULL, NULL, '{}'::jsonb)
            """,
            ford, secret,
        )
    return ford, secret


@pgmark
@pytest.mark.asyncio
async def test_export_import_round_trips_every_row_and_relinks_companies(core_schema, tmp_path):
    await _seed(core_schema.pool)
    path = tmp_path / "seed.jsonl"
    out = await backup.export_seed(core_schema.pool, path)
    assert (out.company, out.alias, out.token) == (2, 2, 3)

    # Wipe and restore. New company ids are minted, so a surviving link proves
    # the natural key did the remapping rather than a copied integer.
    async with core_schema.pool.acquire() as conn:
        await conn.execute("DELETE FROM token")
        await conn.execute("DELETE FROM company_alias")
        await conn.execute("DELETE FROM company")
        # Force different ids on the way back in.
        await conn.execute("ALTER TABLE company ALTER COLUMN id RESTART WITH 9000")

    res = await backup.import_seed(core_schema.pool, path)
    assert (res.company_new, res.alias_new, res.token_new) == (2, 2, 3)
    assert res.skipped_unknown_company == []

    async with core_schema.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT t.ats, t.token, t.config, t.note, t.tags, c.name, c.domain "
            "FROM token t LEFT JOIN company c ON c.id = t.company_id ORDER BY t.ats, t.token"
        )
    by_key = {(r["ats"], r["token"]): r for r in rows}
    ford = by_key[("eightfold", "ford")]
    assert ford["name"] == "Ford" and ford["domain"] == "ford.com"
    assert ford["config"] == {"host": "ford.eightfold.ai"}
    assert ford["note"] == "keep" and ford["tags"] == ["a"]
    # The domainless company relinked through the name: fallback key.
    assert by_key[("comeet", "26.004")]["name"] == "Stealth Co"
    assert by_key[("comeet", "26.004")]["config"] == {"token": "SECRET"}
    # A token with no company survives as NULL rather than being dropped.
    assert by_key[("greenhouse", "orphan")]["name"] is None


@pgmark
@pytest.mark.asyncio
async def test_reexport_of_restored_data_is_byte_identical(core_schema, tmp_path):
    await _seed(core_schema.pool)
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    await backup.export_seed(core_schema.pool, first)
    await backup.import_seed(core_schema.pool, first)
    await backup.export_seed(core_schema.pool, second)
    # Skip the manifest: it carries an export timestamp that moves every run.
    assert first.read_text().splitlines()[1:] == second.read_text().splitlines()[1:]


@pgmark
@pytest.mark.asyncio
async def test_import_restores_config_but_leaves_operator_state_alone(core_schema, tmp_path):
    await _seed(core_schema.pool)
    path = tmp_path / "seed.jsonl"
    await backup.export_seed(core_schema.pool, path)

    # An operator acts after the export: mutes the token and clears its config.
    async with core_schema.pool.acquire() as conn:
        await conn.execute(
            "UPDATE token SET muted = true, status = 'dead', note = 'local', "
            "config = '{}'::jsonb WHERE ats = 'eightfold'"
        )

    await backup.import_seed(core_schema.pool, path)
    async with core_schema.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM token WHERE ats = 'eightfold'")
    # config comes back -- nothing else can supply it ...
    assert row["config"] == {"host": "ford.eightfold.ai"}
    # ... but the operator's decision stands.
    assert row["muted"] is True and row["status"] == "dead" and row["note"] == "local"


@pgmark
@pytest.mark.asyncio
async def test_replace_overwrites_operator_state(core_schema, tmp_path):
    await _seed(core_schema.pool)
    path = tmp_path / "seed.jsonl"
    await backup.export_seed(core_schema.pool, path)
    async with core_schema.pool.acquire() as conn:
        await conn.execute("UPDATE token SET muted = true, status = 'dead' WHERE ats = 'eightfold'")

    await backup.import_seed(core_schema.pool, path, replace=True)
    async with core_schema.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM token WHERE ats = 'eightfold'")
    assert row["muted"] is False and row["status"] == "live"


@pgmark
@pytest.mark.asyncio
async def test_dry_run_reports_without_writing(core_schema, tmp_path):
    await _seed(core_schema.pool)
    path = tmp_path / "seed.jsonl"
    await backup.export_seed(core_schema.pool, path)
    async with core_schema.pool.acquire() as conn:
        await conn.execute("DELETE FROM token WHERE ats = 'eightfold'")

    with pytest.raises(backup._Rollback) as caught:
        await backup.import_seed(core_schema.pool, path, dry_run=True)
    assert caught.value.result.token_new == 1

    async with core_schema.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM token WHERE ats = 'eightfold'") == 0


@pgmark
@pytest.mark.asyncio
async def test_export_refuses_when_two_companies_share_a_natural_key(core_schema, tmp_path):
    async with core_schema.pool.acquire() as conn:
        # Both domainless and same name modulo case: one natural key, two rows.
        await conn.execute("INSERT INTO company (name) VALUES ('Acme'), ('ACME')")
    with pytest.raises(ValueError, match="share a natural key"):
        await backup.export_seed(core_schema.pool, tmp_path / "seed.jsonl")


@pgmark
@pytest.mark.asyncio
async def test_import_is_idempotent(core_schema, tmp_path):
    await _seed(core_schema.pool)
    path = tmp_path / "seed.jsonl"
    await backup.export_seed(core_schema.pool, path)
    await backup.import_seed(core_schema.pool, path)
    second = await backup.import_seed(core_schema.pool, path)
    assert (second.company_new, second.alias_new, second.token_new) == (0, 0, 0)
    async with core_schema.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM company") == 2
        assert await conn.fetchval("SELECT count(*) FROM token") == 3
