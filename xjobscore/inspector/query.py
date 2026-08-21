"""Ad-hoc read-only SQL over the pipeline database, for the Query page.

Telemetry-style: borrows the pipeline pool (see ``PolicyStore.pool_or_none``)
and opens nothing of its own. Unlike every other reader in this package, the
SQL text here comes from an admin's keyboard, not from a parameter the route
already validated -- so this module carries its own defense in depth, in
three independent layers documented on ``run()``. Each layer alone is a
bypassable string check; together they mean a write cannot happen even if the
first two are outsmarted.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

# Server-side row cap. Enforced by only ever fetching this many (+1, to
# detect truncation) from the portal -- the server never materializes more.
MAX_ROWS = 500
# Liveness guard against a runaway query (a cross join, pg_sleep, ...).
STATEMENT_TIMEOUT_MS = 10_000
# Per-cell truncation so one huge job_posting.description can't blow up the
# response; the full value is still one click away via the row detail view.
CELL_CHARS = 10_000

# Layer 1: only statements that start a read. ``with`` covers CTEs, which
# themselves may not write -- layer 3 (the READ ONLY transaction) is what
# actually stops a data-modifying CTE, not this list.
ALLOWED_STARTS = ("select", "with", "table", "explain")

_LEADING_COMMENT_RE = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/\s*)*", re.DOTALL)
_FIRST_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def validate(sql: str) -> str:
    """Strip leading comments/whitespace and a trailing ``;``; check the verb.

    Raises ``ValueError`` (the route maps this to 400) for anything that is
    not read-shaped by its leading keyword. This is advisory, not the safety
    boundary -- see ``run()``'s module-level layers -- but it turns the
    common mistake (pasting an UPDATE) into an instant, specific message
    instead of a database round trip.
    """
    stripped = _LEADING_COMMENT_RE.sub("", sql).strip()
    stripped = stripped.rstrip(";").strip()
    if not stripped:
        raise ValueError("Query is empty.")
    match = _FIRST_WORD_RE.match(stripped)
    verb = match.group(0).lower() if match else ""
    if verb not in ALLOWED_STARTS:
        raise ValueError(
            "Only SELECT, WITH, TABLE, or EXPLAIN statements are allowed "
            f"(got {verb or stripped[:20]!r})."
        )
    return stripped


def _cell(value: Any) -> Any:
    """Coerce one result cell to something ``json`` can encode."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > CELL_CHARS:
            return value[:CELL_CHARS] + "…"
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        # The pipeline pool installs a jsonb codec (see store.py), so jsonb
        # columns already arrive decoded.
        return value
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    return str(value)


async def run(pool: Any, sql: str) -> dict[str, Any]:
    """Execute exactly one read-only statement; never commits.

    Three independent layers keep this from ever writing, so a bypass of any
    one still cannot mutate data:

    1. ``validate()`` above -- the statement must start with a read verb.
    2. Single statement, by protocol: ``conn.prepare()`` uses asyncpg's
       extended query protocol, which PostgreSQL refuses to run more than
       one command through (``SELECT 1; SELECT 2`` fails server-side rather
       than silently running both).
    3. The whole thing runs inside ``START TRANSACTION READ ONLY`` --
       enforced by PostgreSQL itself (SQLSTATE 25006), so a data-modifying
       CTE like ``WITH d AS (DELETE FROM x RETURNING *) SELECT * FROM d``,
       which *does* pass layer 1, still fails -- and the transaction is
       rolled back in ``finally``, never committed, even if it somehow
       hadn't.
    """
    statement = validate(sql)
    async with pool.acquire() as conn:
        transaction = conn.transaction(readonly=True)
        await transaction.start()
        try:
            await conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            started = time.perf_counter()
            prepared = await conn.prepare(statement)
            attributes = prepared.get_attributes()
            cursor = await prepared.cursor()
            records = await cursor.fetch(MAX_ROWS + 1)
            elapsed_ms = (time.perf_counter() - started) * 1000
        finally:
            await transaction.rollback()

    truncated = len(records) > MAX_ROWS
    rows = records[:MAX_ROWS]
    return {
        "columns": [attr.name for attr in attributes],
        "types": [attr.type.name for attr in attributes],
        "rows": [[_cell(value) for value in record] for record in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "max_rows": MAX_ROWS,
    }


_SCHEMA_SQL = """
SELECT
    t.table_name,
    c.column_name,
    c.data_type,
    c.ordinal_position,
    COALESCE(s.n_live_tup, 0) AS approx_rows
FROM information_schema.tables AS t
JOIN information_schema.columns AS c
  ON c.table_schema = t.table_schema AND c.table_name = t.table_name
LEFT JOIN pg_stat_user_tables AS s
  ON s.schemaname = t.table_schema AND s.relname = t.table_name
WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name, c.ordinal_position
"""


async def schema(pool: Any) -> dict[str, Any]:
    """Public base tables, their columns, and an approximate row count.

    ``pg_stat_user_tables.n_live_tup`` is a planner estimate, refreshed by
    autovacuum/analyze -- exact enough to sort by size, not a substitute for
    ``count(*)``.
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(_SCHEMA_SQL)

    tables: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        name = record["table_name"]
        table = tables.get(name)
        if table is None:
            table = {
                "name": name,
                "approx_rows": record["approx_rows"],
                "columns": [],
            }
            tables[name] = table
            order.append(name)
        table["columns"].append(
            {"name": record["column_name"], "type": record["data_type"]}
        )
    return {"tables": [tables[name] for name in order]}


def format_error(exc: asyncpg.PostgresError) -> dict[str, Any]:
    """Shape a PostgreSQL error for display: message, position, hint."""
    if isinstance(exc, asyncpg.QueryCanceledError):
        return {
            "message": f"Statement timed out after {STATEMENT_TIMEOUT_MS // 1000}s.",
            "position": None,
            "hint": None,
        }
    position = getattr(exc, "position", None)
    return {
        "message": str(exc),
        "position": int(position) if position else None,
        "hint": getattr(exc, "hint", None),
    }
