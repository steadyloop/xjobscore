from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from keyscan.core import Keyscan
from xjobscore.keyscan_import import import_keyscan_tokens, load_pending, resolve_ats, resolve_key

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


def _keyscan_db(tmp_path: Path) -> Path:
    """A real keyscan.sqlite (current schema, via Keyscan.initialize)."""
    scan = Keyscan(tmp_path / "keyscan-work")
    scan.initialize()
    return scan.db_path


def _seed_token(
    db_path: Path,
    *,
    ats: str,
    token: str,
    status: str = "live",
    canonical_url: str | None = None,
    discovered_at: str = "2026-01-01T00:00:00+00:00",
    checked_at: str | None = "2026-01-02T00:00:00+00:00",
) -> None:
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO tenants(ats, key, status, canonical_url, discovered_at, checked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ats, token, status, canonical_url or f"https://example.test/{token}", discovered_at, checked_at),
        )
        con.commit()
    finally:
        con.close()


def test_resolve_ats_maps_the_stale_workday_label():
    assert resolve_ats("workday") == "workday"
    assert resolve_ats("greenhouse") == "greenhouse"


def test_resolve_key_uses_the_tenant_segment_for_compound_tokens():
    assert resolve_key("workday", "acme/wd5/AcmeCareers") == "acme"
    assert resolve_key("oracle", "acme/12345") == "acme"
    assert resolve_key("greenhouse", "acme") == "acme"


@pytest.mark.asyncio
async def test_only_resolvable_rows_are_written_with_full_field_mapping(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    db_path = _keyscan_db(tmp_path)
    resolvable_token = f"acme-{suffix}"
    unresolvable_token = f"totally-unknown-{suffix}"
    workday_token = f"acme-{suffix}/wd5/AcmeCareers"

    _seed_token(db_path, ats="greenhouse", token=resolvable_token, status="live")
    _seed_token(db_path, ats="greenhouse", token=unresolvable_token, status="live")
    # keyscan's stale pre-rename label -- must land under workday.
    _seed_token(db_path, ats="workday", token=workday_token, status="dead", checked_at=None)

    pool = core_schema.pool
    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            f"Acme {suffix}",
            f"acme-{suffix}.test",
        )
        await conn.execute(
            "INSERT INTO company_alias(alias_norm, company_id, alias) VALUES ($1, $2, $1)",
            f"acme-{suffix}",
            company_id,
        )

    result = await import_keyscan_tokens(pool, db_path)

    assert result.pending == 3
    assert result.resolved == 2
    assert result.written == 2

    async with pool.acquire() as conn:
        greenhouse_row = await conn.fetchrow(
            "SELECT * FROM token WHERE ats = 'greenhouse' AND token = $1", resolvable_token
        )
        workday_row = await conn.fetchrow(
            "SELECT * FROM token WHERE ats = 'workday' AND token = $1",
            workday_token.lower(),
        )
        missing = await conn.fetchrow(
            "SELECT * FROM token WHERE ats = 'greenhouse' AND token = $1", unresolvable_token
        )

    assert greenhouse_row["company_id"] == company_id
    assert greenhouse_row["status"] == "live"
    assert greenhouse_row["source"] == "keyscan"
    assert greenhouse_row["checked_at"] is not None
    assert greenhouse_row["discovered_at"] is not None
    assert greenhouse_row["imported_at"] is not None

    assert workday_row["company_id"] == company_id
    assert workday_row["status"] == "dead"
    assert workday_row["checked_at"] is None

    assert missing is None  # unresolved: skipped entirely, not written with NULL company_id

    # keyscan's own imported_at is set only for the rows actually written,
    # scoped to keyscan's original ats label ("workday", not "workday").
    pending_after = load_pending(db_path)
    assert {row.token for row in pending_after} == {unresolvable_token}


@pytest.mark.asyncio
async def test_rerun_is_idempotent(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    db_path = _keyscan_db(tmp_path)
    token = f"acme-{suffix}"
    _seed_token(db_path, ats="greenhouse", token=token, status="live")

    pool = core_schema.pool
    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            f"Acme {suffix}",
            f"acme-{suffix}.test",
        )
        await conn.execute(
            "INSERT INTO company_alias(alias_norm, company_id, alias) VALUES ($1, $2, $1)",
            f"acme-{suffix}",
            company_id,
        )

    first = await import_keyscan_tokens(pool, db_path)
    second = await import_keyscan_tokens(pool, db_path)

    assert first.written == 1
    assert second.pending == 0
    assert second.written == 0


@pytest.mark.asyncio
async def test_a_row_not_owned_by_keyscan_is_never_overwritten(tmp_path, core_schema):
    """Keyscan must not overwrite a token with NULL or another source."""
    suffix = uuid4().hex[:8]
    db_path = _keyscan_db(tmp_path)
    token = f"acme-{suffix}"
    _seed_token(db_path, ats="greenhouse", token=token, status="dead")

    pool = core_schema.pool
    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            f"Acme {suffix}",
            f"acme-{suffix}.test",
        )
        await conn.execute(
            "INSERT INTO company_alias(alias_norm, company_id, alias) VALUES ($1, $2, $1)",
            f"acme-{suffix}",
            company_id,
        )
        # Simulate the native pipeline already owning this token, disagreeing
        # with keyscan's status.
        await conn.execute(
            "INSERT INTO token(ats, token, company_id, status) VALUES ('greenhouse', $1, $2, 'live')",
            token,
            company_id,
        )

    result = await import_keyscan_tokens(pool, db_path)
    assert result.resolved == 1
    assert result.written == 0  # declined: not keyscan-owned

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM token WHERE ats = 'greenhouse' AND token = $1", token)
    assert row["status"] == "live"  # untouched
    assert row["source"] is None

    # Declined, not silently marked done -- stays retryable on keyscan's side.
    assert {r.token for r in load_pending(db_path)} == {token}


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    db_path = _keyscan_db(tmp_path)
    token = f"acme-{suffix}"
    _seed_token(db_path, ats="greenhouse", token=token, status="live")

    pool = core_schema.pool
    async with pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            f"Acme {suffix}",
            f"acme-{suffix}.test",
        )
        await conn.execute(
            "INSERT INTO company_alias(alias_norm, company_id, alias) VALUES ($1, $2, $1)",
            f"acme-{suffix}",
            company_id,
        )

    result = await import_keyscan_tokens(pool, db_path, dry_run=True)
    assert result.resolved == 1
    assert result.written == 0

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM token WHERE ats = 'greenhouse' AND token = $1", token)
    assert row is None
    # Still pending on the keyscan side -- a dry run must not mark anything imported.
    assert {r.token for r in load_pending(db_path)} == {token}
