from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from keyscan.core import Keyscan
from xjobscore.keyscan_seed import (
    Candidate,
    load_candidates,
    seed_keyscan_tokens,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


def _keyscan_db(tmp_path: Path) -> Path:
    scan = Keyscan(tmp_path / "keyscan-work")
    scan.initialize()
    return scan.db_path


def _seed_tenant(db_path: Path, *, ats: str, token: str, name: str | None = None) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO tenants(ats, key, status, canonical_url, discovered_at, name)
               VALUES (?, ?, 'live', ?, '2026-01-01T00:00:00+00:00', ?)""",
            (ats, token, f"https://example.test/{token}", name),
        )
        con.commit()
    finally:
        con.close()


def _imported_at(db_path: Path, ats: str, token: str) -> str | None:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT imported_at FROM tenants WHERE ats = ? AND key = ?", (ats, token)
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _candidate(ats: str, token: str, name: str = "") -> Candidate:
    return Candidate(keyscan_ats=ats, ats=ats, token=token, name=name)


def test_load_candidates_rejects_duplicates(tmp_path):
    path = tmp_path / "c.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ats", "token", "name"])
        writer.writerow(["greenhouse", "acme", "Acme"])
        writer.writerow(["greenhouse", "acme", "Acme Again"])
    with pytest.raises(ValueError, match="duplicate"):
        load_candidates(path)


def test_load_candidates_rejects_a_blank_token(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("ats,token,name\ngreenhouse,,Acme\n")
    with pytest.raises(ValueError, match="blank"):
        load_candidates(path)


@pytest.mark.asyncio
async def test_mints_a_company_with_aliases_token_and_feed(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"windborne-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name="WindBorne Systems")

    result = await seed_keyscan_tokens(
        core_schema.pool,
        [_candidate("greenhouse", token, "WindBorne Systems")],
        db_path=db_path,
    )

    assert (result.minted, result.resolved, result.tokens_written) == (1, 0, 1)
    assert result.feeds_created == 1
    assert result.marked == 1

    async with core_schema.pool.acquire() as conn:
        company = await conn.fetchrow(
            "SELECT * FROM company WHERE name = 'WindBorne Systems'"
        )
        # Pending, because a scraped board title is a guess and not a verdict.
        assert company["reviewed"] == "pending"
        assert company["domain"] is None
        assert company["discovered_from"] == "keyscan"
        assert company["tags"]

        token_row = await conn.fetchrow(
            "SELECT * FROM token WHERE ats = 'greenhouse' AND token = $1", token
        )
        assert token_row["company_id"] == company["id"]
        assert token_row["source"] == "keyscan"
        assert token_row["imported_at"] is not None

        feed = await conn.fetchrow(
            "SELECT * FROM feed WHERE source = 'greenhouse' AND key = $1", token
        )
        assert feed["polling"] is True

        # Both the display name and the slug resolve: silver sees the slug on
        # the wire, and without that alias the postings this import exists to
        # collect could not find the row just minted for them.
        aliases = {
            row["alias_norm"]
            for row in await conn.fetch(
                "SELECT alias_norm FROM company_alias WHERE company_id = $1",
                company["id"],
            )
        }
    assert "windborne systems" in aliases
    assert token in aliases
    assert _imported_at(db_path, "greenhouse", token) is not None


@pytest.mark.asyncio
async def test_a_nameless_board_is_minted_under_its_slug(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"nameless-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="lever", token=token)

    result = await seed_keyscan_tokens(
        core_schema.pool, [_candidate("lever", token)], db_path=db_path
    )

    assert result.minted == 1
    async with core_schema.pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT c.name FROM company c JOIN token t ON t.company_id = c.id"
            " WHERE t.ats = 'lever' AND t.token = $1",
            token,
        )
    # NOT NULL and printed on every posting: the ugly slug is the honest
    # placeholder, and it is what puts the row at the top of the review queue.
    assert name == token


@pytest.mark.asyncio
async def test_an_existing_company_is_reused_never_duplicated(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"acme-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name="Acme")

    async with core_schema.pool.acquire() as conn:
        company_id = await conn.fetchval(
            "INSERT INTO company(name) VALUES ($1) RETURNING id", f"Acme {suffix}"
        )
        await conn.execute(
            "INSERT INTO company_alias(alias_norm, company_id, alias) VALUES ($1, $2, $1)",
            token,
            company_id,
        )

    result = await seed_keyscan_tokens(
        core_schema.pool, [_candidate("greenhouse", token, "Acme")], db_path=db_path
    )

    assert (result.resolved, result.minted) == (1, 0)
    async with core_schema.pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT company_id FROM token WHERE ats='greenhouse' AND token=$1", token
            )
            == company_id
        )


@pytest.mark.asyncio
async def test_two_tokens_for_one_employer_share_a_single_company(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    first, second = f"acme-jobs-{suffix}", f"acme-careers-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=first, name=f"Acme {suffix}")
    _seed_tenant(db_path, ats="lever", token=second, name=f"Acme {suffix}")

    result = await seed_keyscan_tokens(
        core_schema.pool,
        [
            _candidate("greenhouse", first, f"Acme {suffix}"),
            _candidate("lever", second, f"Acme {suffix}"),
        ],
        db_path=db_path,
    )

    # The second token resolves through the alias the first one just wrote --
    # the in-memory map has to see an insert made inside this same transaction.
    assert (result.minted, result.resolved) == (1, 1)
    async with core_schema.pool.acquire() as conn:
        ids = await conn.fetch(
            "SELECT DISTINCT company_id FROM token WHERE token = ANY($1::text[])",
            [first, second],
        )
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_feeds_off_leaves_polling_disabled(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"dormant-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name=f"Dormant {suffix}")

    await seed_keyscan_tokens(
        core_schema.pool,
        [_candidate("greenhouse", token, f"Dormant {suffix}")],
        db_path=db_path,
        enable_feeds=False,
    )

    async with core_schema.pool.acquire() as conn:
        polling = await conn.fetchval(
            "SELECT polling FROM feed WHERE source='greenhouse' AND key=$1", token
        )
    assert polling is False


@pytest.mark.asyncio
async def test_dry_run_writes_nothing_on_either_side(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"ghost-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name=f"Ghost {suffix}")

    result = await seed_keyscan_tokens(
        core_schema.pool,
        [_candidate("greenhouse", token, f"Ghost {suffix}")],
        db_path=db_path,
        dry_run=True,
    )

    assert result.minted == 1
    assert (result.tokens_written, result.feeds_created, result.marked) == (0, 0, 0)
    async with core_schema.pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM company WHERE name = $1", f"Ghost {suffix}"
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM token WHERE token = $1", token
            )
            == 0
        )
    assert _imported_at(db_path, "greenhouse", token) is None


@pytest.mark.asyncio
async def test_tag_marks_the_batch_for_teardown(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"recon-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name=f"Recon {suffix}")

    await seed_keyscan_tokens(
        core_schema.pool,
        [_candidate("greenhouse", token, f"Recon {suffix}")],
        db_path=db_path,
        tag="recon",
    )

    async with core_schema.pool.acquire() as conn:
        tags = await conn.fetchval(
            "SELECT tags FROM company WHERE name = $1", f"Recon {suffix}"
        )
        # `tags ? 'recon'` is the selector both the UI exclusion and the
        # teardown use, so the key has to be present, not merely truthy.
        tagged = await conn.fetchval(
            "SELECT count(*) FROM company WHERE tags ? 'recon'"
        )
    assert "recon" in tags
    assert tags["name_machine"] == f"Recon {suffix}"
    assert tagged == 1


@pytest.mark.asyncio
async def test_untagged_batches_carry_no_teardown_key(tmp_path, core_schema):
    suffix = uuid4().hex[:8]
    token = f"keeper-{suffix}"
    db_path = _keyscan_db(tmp_path)
    _seed_tenant(db_path, ats="greenhouse", token=token, name=f"Keeper {suffix}")

    await seed_keyscan_tokens(
        core_schema.pool,
        [_candidate("greenhouse", token, f"Keeper {suffix}")],
        db_path=db_path,
    )

    async with core_schema.pool.acquire() as conn:
        tags = await conn.fetchval(
            "SELECT tags FROM company WHERE name = $1", f"Keeper {suffix}"
        )
    assert list(tags) == ["name_machine"]
