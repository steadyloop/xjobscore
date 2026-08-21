"""Unit tests for query.py -- no database.

The point of this module is that a write cannot happen even if one of its
defenses is individually bypassed, so these tests check each layer on its
own: ``validate()``'s allowlist, and separately, that ``run()`` always opens
its connection's transaction ``readonly=True`` and always rolls back, never
commits, regardless of outcome.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from xjobscore.inspector import query

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------- validate()

@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "  select 1  ",
        "select 1;",
        "SELECT 1",
        "with x as (select 1) select * from x",
        "table job_posting",
        "explain select 1",
        "-- a comment\nselect 1",
        "/* block comment */ select 1",
    ],
)
def test_validate_accepts_read_statements(sql):
    assert query.validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "update job_posting set status = 'closed'",
        "delete from job_posting",
        "insert into company (name) values ('x')",
        "drop table company",
        "vacuum",
        "grant select on job_posting to public",
        "",
        "   ",
        "-- just a comment",
    ],
)
def test_validate_rejects_everything_else(sql):
    with pytest.raises(ValueError):
        query.validate(sql)


def test_validate_strips_trailing_semicolon_and_whitespace():
    assert query.validate("  select 1;  \n") == "select 1"


# ------------------------------------------------------------------ _cell()

def test_cell_passes_through_json_safe_scalars():
    assert query._cell(None) is None
    assert query._cell(True) is True
    assert query._cell(1) == 1
    assert query._cell(1.5) == 1.5
    assert query._cell("hi") == "hi"
    assert query._cell({"a": 1}) == {"a": 1}
    assert query._cell([1, 2]) == [1, 2]


def test_cell_coerces_datetime_and_date_to_iso():
    assert query._cell(NOW) == NOW.isoformat()
    assert query._cell(date(2026, 8, 15)) == "2026-08-15"


def test_cell_coerces_uuid_and_decimal_to_str():
    id_ = UUID("00000000-0000-0000-0000-000000000001")
    assert query._cell(id_) == str(id_)
    assert query._cell(Decimal("1.50")) == "1.50"


def test_cell_hex_encodes_bytes():
    assert query._cell(b"\x00\xff") == "\\x00ff"


def test_cell_truncates_long_strings():
    long_value = "x" * (query.CELL_CHARS + 10)
    result = query._cell(long_value)
    assert len(result) == query.CELL_CHARS + 1
    assert result.endswith("…")


# ---------------------------------------------------------------------- run()

class FakeCursor:
    def __init__(self, records):
        self._records = records

    async def fetch(self, n):
        return self._records[:n]


class FakeAttr:
    def __init__(self, name, type_name):
        self.name = name
        self.type = type("Type", (), {"name": type_name})


class FakePrepared:
    def __init__(self, attrs, records):
        self._attrs = attrs
        self._records = records

    def get_attributes(self):
        return self._attrs

    async def cursor(self):
        return FakeCursor(self._records)


class FakeTransaction:
    def __init__(self, connection, *, readonly):
        self.connection = connection
        self.readonly = readonly
        self.started = False
        self.rolled_back = False
        self.committed = False

    async def start(self):
        self.started = True

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):  # pragma: no cover -- must never be called
        self.committed = True


class FakeConnection:
    def __init__(self, attrs, records):
        self.executed: list[str] = []
        self.prepared_sql: str | None = None
        self.transactions: list[FakeTransaction] = []
        self._attrs = attrs
        self._records = records
        self._prepare_error: Exception | None = None

    def transaction(self, *, readonly=False, **kwargs):
        tr = FakeTransaction(self, readonly=readonly)
        self.transactions.append(tr)
        return tr

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def prepare(self, sql):
        self.prepared_sql = sql
        if self._prepare_error is not None:
            raise self._prepare_error
        return FakePrepared(self._attrs, self._records)


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


async def test_run_starts_readonly_transaction_and_always_rolls_back():
    conn = FakeConnection(attrs=[FakeAttr("id", "int8")], records=[(1,)])
    pool = FakePool(conn)

    result = await query.run(pool, "select id from job_posting")

    assert result["columns"] == ["id"]
    assert result["rows"] == [[1]]
    [transaction] = conn.transactions
    assert transaction.readonly is True
    assert transaction.started is True
    assert transaction.rolled_back is True
    assert transaction.committed is False
    assert any("statement_timeout" in sql for sql in conn.executed)


async def test_run_rolls_back_even_when_prepare_fails():
    conn = FakeConnection(attrs=[], records=[])
    conn._prepare_error = asyncpg.PostgresSyntaxError("syntax error")
    pool = FakePool(conn)

    with pytest.raises(asyncpg.PostgresSyntaxError):
        await query.run(pool, "select bad syntax")

    [transaction] = conn.transactions
    assert transaction.rolled_back is True
    assert transaction.committed is False


async def test_run_reports_truncation_past_max_rows():
    records = [(i,) for i in range(query.MAX_ROWS + 1)]
    conn = FakeConnection(attrs=[FakeAttr("n", "int8")], records=records)
    pool = FakePool(conn)

    result = await query.run(pool, "select n from generate_series(1, 501) n")

    assert result["row_count"] == query.MAX_ROWS
    assert result["truncated"] is True
    assert len(result["rows"]) == query.MAX_ROWS


async def test_run_rejects_non_read_sql_without_touching_the_pool():
    conn = FakeConnection(attrs=[], records=[])
    pool = FakePool(conn)

    with pytest.raises(ValueError):
        await query.run(pool, "update job_posting set status = 'closed'")

    assert conn.transactions == []
    assert conn.prepared_sql is None


# ------------------------------------------------------------- format_error()

def test_format_error_reports_timeout_by_name():
    exc = asyncpg.QueryCanceledError("canceling statement due to statement timeout")
    shaped = query.format_error(exc)
    assert "timed out" in shaped["message"]


def test_format_error_carries_position():
    exc = asyncpg.PostgresSyntaxError("syntax error at or near")
    exc.position = "8"
    shaped = query.format_error(exc)
    assert shaped["position"] == 8
