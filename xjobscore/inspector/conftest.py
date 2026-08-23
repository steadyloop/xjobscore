"""Throwaway-schema fixtures for the PostgreSQL-backed inspector store.

Each store gets its own ``inspector_test_*`` schema in a real database and
drops it on teardown, so tests never see one another's rows and never touch
the core pipeline's ``public`` tables. Without a reachable database the whole
inspector suite skips rather than failing.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import pathlib

import pytest

from xjobscore.inspector import config
from xjobscore.inspector.persistence import ResultStore

_schema_counter = itertools.count()


@pytest.fixture(autouse=True)
def _runner_stays_off(monkeypatch):
    """No test process may start the real polling loop.

    ``config.runner_enabled()`` reads ``XJOBSCORE_INSPECTOR_RUNNER``, and
    ``config._load_local_config()`` pulls the repo's ``.env`` into the
    environment at import time. A developer who has enabled the runner for
    their own inspector would otherwise have every ``create_app`` in this
    suite spawn a task that polls live feeds and writes ``public``. Cleared
    here rather than in individual fixtures so it cannot be forgotten; a test
    that wants the runner on sets it back explicitly.
    """
    monkeypatch.delenv("XJOBSCORE_INSPECTOR_RUNNER", raising=False)
    monkeypatch.delenv("XJOBSCORE_INSPECTOR_TOKEN_HEARTBEAT", raising=False)


def _staging_ddl() -> str:
    """The fetched_feed / fetched_feed_posting DDL, read out of schema.sql.

    Sliced from the file rather than copied, so a column added to the real
    staging tables cannot silently go missing under test. The statements are
    unqualified, so they land in whatever schema search_path points at.
    """
    root = pathlib.Path(__file__).resolve().parents[2] / "schema.sql"
    text = root.read_text()
    start = text.index("CREATE TABLE fetched_feed")
    end = text.index("-- JOB POSTING - BRONZE", start)
    return text[start:end]


def store_dsn() -> str | None:
    """Prefer an explicit test DSN, then the inspector's configured database."""
    return (
        os.environ.get("XJOBSCORE_TEST_DSN")
        or os.environ.get("XJOBSCORE_INSPECTOR_STORE_DSN")
        or config.dsn_or_none()
    )


@pytest.fixture
async def store_factory():
    """Build ResultStores on isolated schemas, dropped when the test ends."""
    dsn = store_dsn()
    if dsn is None:
        pytest.skip("No PostgreSQL DSN; set XJOBSCORE_TEST_DSN to run these tests.")
    built: list[ResultStore] = []

    async def make(**kwargs) -> ResultStore:
        schema = f"inspector_test_{os.getpid()}_{next(_schema_counter)}"
        # Staging goes in the throwaway schema too, so a test can never write
        # the pipeline's real fetched_feed rows.
        store = ResultStore(dsn, schema=schema, staging=schema, **kwargs)
        try:
            await store.create_schema()
            pool = await store.pool()
            # SET LOCAL, inside a transaction: the connection goes back to the
            # pool with its default search_path intact.
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SET LOCAL search_path TO {schema}")
                await conn.execute(_staging_ddl())
        except OSError as exc:  # unreachable server, not a test failure
            await store.close()
            pytest.skip(f"PostgreSQL unavailable at {dsn}: {exc}")
        built.append(store)
        return store

    yield make

    for store in built:
        pool = await store.pool()
        async with pool.acquire() as conn:
            await conn.execute(f"DROP SCHEMA IF EXISTS {store.schema} CASCADE")
        await store.close()


@pytest.fixture
async def store(store_factory):
    """A ready-to-use store on its own schema."""
    return await store_factory()


@pytest.fixture
def app_store():
    """A store on a throwaway schema for TestClient-driven route tests.

    Sync on purpose: TestClient runs the app's lifespan on an event loop of
    its own, and ResultStore builds its pool lazily inside that lifespan, so
    nothing here needs to await. Teardown opens a short-lived pool because the
    app already closed the one it used.
    """
    dsn = store_dsn()
    if dsn is None:
        pytest.skip("No PostgreSQL DSN; set XJOBSCORE_TEST_DSN to run these tests.")
    schema = f"inspector_test_{os.getpid()}_{next(_schema_counter)}"

    async def setup() -> None:
        # The app's lifespan creates this store's own tables, but the staging
        # tables belong to schema.sql. Create them here so a route test stages
        # into its throwaway schema instead of the pipeline's real rows.
        store = ResultStore(dsn, schema=schema, staging=schema)
        try:
            await store.create_schema()
            pool = await store.pool()
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SET LOCAL search_path TO {schema}")
                await conn.execute(_staging_ddl())
        except OSError as exc:
            pytest.skip(f"PostgreSQL unavailable at {dsn}: {exc}")
        finally:
            await store.close()

    asyncio.run(setup())

    yield ResultStore(dsn, schema=schema, staging=schema)

    async def drop() -> None:
        store = ResultStore(dsn, schema=schema, staging=schema)
        try:
            pool = await store.pool()
            async with pool.acquire() as conn:
                await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await store.close()

    asyncio.run(drop())
