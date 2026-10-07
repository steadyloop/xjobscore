"""Throwaway-schema fixture for the core pipeline's PostgreSQL integration test.

The core pipeline's tables are unqualified, so a connection whose ``search_path``
points at a private schema exercises the real ``claim_due_feeds`` / ``poll``
path without touching a developer's actual feeds. That isolation is not a
nicety: ``claim_due_feeds`` takes ``LIMIT n`` off the *global* due queue, so a
test run against a populated database claims whichever real feeds happen to be
due, polls them through its fake harvester, and leaves their feed state and bronze
rows corrupted. Cleaning up by source name cannot undo that, because the rows
belong to feeds the test never created.

Mirrors ``inspector/conftest.py``'s per-test schema pattern, with one
difference: ``schema.sql`` wraps itself in ``BEGIN``/``COMMIT``, so the
search_path is set at session level on a dedicated connection rather than with
``SET LOCAL`` -- the file's own COMMIT would end an outer transaction and
discard a LOCAL setting halfway through the DDL.
"""

from __future__ import annotations

import itertools
import os
import pathlib
from dataclasses import dataclass

import asyncpg
import pytest

from xjobscore import store

_schema_counter = itertools.count()


@pytest.fixture
def live_migration():
    """Read a `xjobscore/migrations/` file, skipping the test when it is absent.

    That directory is applied against the live database and deliberately
    untracked (.gitignore), so a checkout without the file has nothing to check.
    """
    root = pathlib.Path(__file__).resolve().parent / "migrations"

    def read(name: str) -> str:
        path = root / name
        if not path.is_file():
            pytest.skip(f"untracked migration {name} not present")
        return path.read_text()

    return read


@dataclass(frozen=True, slots=True)
class CoreSchema:
    """A private schema plus a pool already scoped to it."""

    dsn: str
    schema: str
    pool: asyncpg.Pool


def _schema_sql() -> str:
    """The full core DDL, read from the file rather than duplicated.

    ``schema.sql`` is the documented source of truth, so a column added there
    reaches this fixture without anyone having to remember to mirror it.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"
    return root.read_text()


@pytest.fixture
async def core_schema():
    """Build the core schema in a throwaway namespace; drop it afterwards."""
    dsn = os.environ.get("XJOBSCORE_TEST_DSN")
    if not dsn:
        pytest.skip("set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests")

    schema = f"core_test_{os.getpid()}_{next(_schema_counter)}"
    try:
        setup = await asyncpg.connect(dsn)
    except OSError as exc:  # unreachable server is not a test failure
        pytest.skip(f"PostgreSQL unavailable at {dsn}: {exc}")

    try:
        await setup.execute(f"CREATE SCHEMA {schema}")
        # Session-level, not SET LOCAL: schema.sql commits partway through.
        await setup.execute(f"SET search_path TO {schema}")
        await setup.execute(_schema_sql())
    finally:
        await setup.close()

    pool = await store.connect(dsn, search_path=schema)
    try:
        yield CoreSchema(dsn=dsn, schema=schema, pool=pool)
    finally:
        await pool.close()
        cleanup = await asyncpg.connect(dsn)
        try:
            await cleanup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await cleanup.close()
