"""Unit tests for the Companies review queue over ``public.company``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from xjobscore.console import companies


class FakeCompanyPool:
    """Canned pool: the counts header, then the page of rows.

    ``companies.query`` runs one fetchrow (counts) then one fetch (the page),
    which is the same shape ``FakeTokensPool`` in test_routes.py assumes.
    """

    def __init__(self, rows=(), counts=None, current=None):
        self.rows = list(rows)
        self.counts = counts or {
            "total": 2, "verified": 1, "rejected": 0, "unverified": 1,
            "queue": 1, "no_domain": 0, "serving": 1,
        }
        self.current = current
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        if "INSERT INTO company_alias" in sql:
            return [{"alias_norm": norm} for norm in args[0]]
        return self.rows

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE" in sql:
            return self.current
        if "SELECT c.id" in sql:
            return self.rows[0] if self.rows else None
        return self.counts

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"


def _row(**overrides):
    row = {
        "id": 7, "name": "acme-inc", "domain": "acme.example",
        "description": "makes things", "tags": {},
        "reviewed": "pending", "reviewed_at": None,
        "active_postings": 12, "alias_count": 1, "has_logo": True,
        "tokens": ["greenhouse:acme"], "sample_title": "Intern",
        "sample_apply_url": "https://acme.example/j/1",
        "sample_source_url": None, "total": 1,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_query_shapes_rows_and_derives_review_state():
    pool = FakeCompanyPool(rows=[_row()])
    payload = await companies.query(pool)
    assert payload["total"] == 1
    assert payload["counts"]["queue"] == 1
    row = payload["rows"][0]
    assert row["review_state"] == "unverified"
    assert row["tokens"] == ["greenhouse:acme"]
    # "total" is a window column, not part of the row the page renders.
    assert "total" not in row


@pytest.mark.asyncio
async def test_review_state_reads_verified_then_rejected():
    stamped = datetime(2026, 9, 8, tzinfo=timezone.utc).isoformat()
    verified = FakeCompanyPool(rows=[
        _row(reviewed="verified", reviewed_at=stamped),
    ])
    assert (await verified.fetchrow("SELECT c.id")) is not None
    payload = await companies.query(verified, filter="all")
    assert payload["rows"][0]["review_state"] == "verified"
    # review_at is surfaced as-is: the page renders it as a relative time.
    assert payload["rows"][0]["review_at"].startswith("2026-09-08")

    rejected = FakeCompanyPool(rows=[_row(reviewed="rejected")])
    payload = await companies.query(rejected, filter="rejected")
    assert payload["rows"][0]["review_state"] == "rejected"


@pytest.mark.asyncio
async def test_query_reports_the_original_machine_guess():
    pool = FakeCompanyPool(rows=[
        _row(name="Acme, Inc.", domain="acme.example",
             tags={"name_machine": "acme-inc", "domain_machine": "guess.example"}),
    ])
    row = (await companies.query(pool, filter="all"))["rows"][0]
    assert row["name_corrected"] is True
    assert row["domain_corrected"] is True
    assert row["name_machine"] == "acme-inc"


@pytest.mark.asyncio
async def test_query_rejects_unknown_filter_and_order():
    pool = FakeCompanyPool()
    with pytest.raises(ValueError):
        await companies.query(pool, filter="nope")
    with pytest.raises(ValueError):
        await companies.query(pool, order="nope")


@pytest.mark.asyncio
async def test_pending_is_a_value_not_an_absence():
    """`reviewed` is NOT NULL, so the backlog is an equality, never a NULL test.

    The jsonb version had to spell this `IS NULL` because an unreviewed row
    carried no key at all, and `NOT (NULL = 'rejected')` is NULL -- a predicate
    that silently hid every row this page exists to show.
    """
    assert companies._FILTER_PREDICATES["unverified"] == "c.reviewed = 'pending'"
    assert "NULL" not in companies._FILTER_PREDICATES["unverified"]


@pytest.mark.asyncio
async def test_verify_stamps_records_the_guess_and_writes_both_aliases():
    pool = FakeCompanyPool(
        rows=[_row(name="Acme, Inc.")],
        current={"id": 7, "name": "acme-inc", "domain": "guess.example",
                 "description": "d", "tags": {}},
    )
    await companies.verify(pool, 7, name="Acme, Inc.", domain="acme.example")

    update_sql, update_args = pool.executed[0]
    assert "reviewed = 'verified'" in update_sql
    assert "tags = tags || $6::jsonb" in update_sql
    assert update_args[1] == "Acme, Inc."
    assert update_args[2] == "acme.example"
    patch = update_args[5]
    # Written once so the correction rate stays measurable afterwards.
    assert patch["name_machine"] == "acme-inc"
    assert patch["domain_machine"] == "guess.example"

    alias_sql, alias_args = pool.fetched[0]
    assert "ON CONFLICT (alias_norm) DO NOTHING" in alias_sql
    # The old slug must keep resolving after the rename.
    assert "acme-inc" in alias_args[0]
    assert "acme, inc." in alias_args[0]


@pytest.mark.asyncio
async def test_verify_keeps_an_existing_machine_guess():
    pool = FakeCompanyPool(
        rows=[_row()],
        current={"id": 7, "name": "b", "domain": "b.example", "description": None,
                 "tags": {"name_machine": "first-guess",
                          "domain_machine": "first.example"}},
    )
    await companies.verify(pool, 7, name="c")
    patch = pool.executed[0][1][5]
    assert "name_machine" not in patch
    assert "domain_machine" not in patch


@pytest.mark.asyncio
async def test_verify_distinguishes_omitted_from_cleared():
    pool = FakeCompanyPool(
        rows=[_row()],
        current={"id": 7, "name": "n", "domain": "keep.example",
                 "description": "keep", "tags": {}},
    )
    await companies.verify(pool, 7)
    assert pool.executed[0][1][2] == "keep.example"

    pool.executed.clear()
    await companies.verify(pool, 7, domain="", description="  ")
    assert pool.executed[0][1][2] is None
    assert pool.executed[0][1][3] is None


@pytest.mark.asyncio
async def test_verify_refuses_a_blank_name_and_missing_row():
    pool = FakeCompanyPool(
        rows=[_row()],
        current={"id": 7, "name": "n", "domain": None, "description": None,
                 "tags": {}},
    )
    with pytest.raises(ValueError):
        await companies.verify(pool, 7, name="   ")
    assert await companies.verify(FakeCompanyPool(current=None), 7) is None


@pytest.mark.asyncio
async def test_reject_marks_the_row_rather_than_deleting_it():
    """job_posting_company_id_fkey is NO ACTION: a DELETE would simply fail."""
    pool = FakeCompanyPool(rows=[_row(reviewed="rejected")])
    await companies.set_review(pool, 7, state="rejected")
    sql, args = pool.executed[0]
    assert "DELETE" not in sql.upper()
    assert "reviewed = 'rejected'" in sql
    assert "tags" not in sql          # the verdict no longer touches the labels


@pytest.mark.asyncio
async def test_reopen_clears_the_verdict_but_keeps_provenance():
    pool = FakeCompanyPool(rows=[_row()])
    await companies.set_review(pool, 7, state="pending")
    sql = pool.executed[0][0]
    assert "reviewed = 'pending'" in sql
    assert "reviewed_at = NULL" in sql
    assert "name_machine" not in sql

    with pytest.raises(ValueError):
        await companies.set_review(pool, 7, state="banana")
