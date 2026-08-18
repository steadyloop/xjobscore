from __future__ import annotations

import sqlite3

import pytest

from xjobscore import feader, store
from xjobscore.sources.registry.loader import Company, Entry

SCHEMA = """
CREATE TABLE company (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT UNIQUE,
    domain_verified_at TEXT,
    description TEXT
);
CREATE TABLE alias (
    alias_norm TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    alias TEXT NOT NULL
);
CREATE TABLE token (
    ats TEXT NOT NULL,
    token TEXT NOT NULL,
    company_id INTEGER REFERENCES company(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('unverified', 'live', 'inactive')),
    PRIMARY KEY (ats, token)
);
"""


def _company(**overrides) -> Company:
    base = {
        "id": 1,
        "name": "Acme",
        "aliases": ("acme",),
        "domain": "unknown",
        "industry": "unknown",
        "desc": "",
        "verified": False,
        "domain_verified_at": None,
    }
    return Company(**{**base, **overrides})


def _entry(**overrides) -> Entry:
    base = {
        "source": "ashby",
        "key": "acme",
        "company_id": 1,
        "company_name": "Acme",
        "domain": "unknown",
        "verified": True,
        "verified_at": "2026-08-04",
        "config": {},
    }
    return Entry(**{**base, **overrides})


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def _load(db: sqlite3.Connection, import_plan: feader.Plan) -> None:
    for company in import_plan.companies:
        db.execute(
            """
            INSERT INTO company(id, name, domain, domain_verified_at, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (company.id, company.name, company.domain, company.domain_verified_at, company.description),
        )
        db.executemany(
            "INSERT INTO alias(alias_norm, company_id, alias) VALUES (?, ?, ?)",
            (
                (alias_norm, company.id, alias_norm)
                for alias_norm in company.aliases
            ),
        )
    db.executemany(
        "INSERT INTO token(ats, token, company_id, status) VALUES (?, ?, ?, ?)",
        (
            (token.source, token.key, token.company_id, token.status)
            for token in import_plan.tokens
        ),
    )


def test_unknown_domain_becomes_null():
    import_plan = feader.plan((_company(),), (_entry(),))

    assert import_plan.companies[0].domain is None


def test_registry_aliases_are_seeded_with_company_slug():
    company = _company(
        id=97,
        aliases=("ambient-ai", "ambient.ai"),
    )

    aliases = feader.plan((company,), ()).companies[0].aliases

    assert aliases == ("ambient-ai", "ambient.ai")


def test_seed_includes_only_verified_tokens_and_feeds():
    import_plan = feader.plan(
        (
            _company(id=1, aliases=("a",)),
            _company(id=2, aliases=("b",)),
        ),
        (
            _entry(key="a", company_id=1),
            _entry(key="b", company_id=2, verified=False),
        ),
    )

    assert [(token.source, token.key, token.status) for token in import_plan.tokens] == [
        ("ashby", "a", "live"),
    ]
    assert [(feed.source, feed.key, feed.enabled) for feed in import_plan.feeds] == [
        ("ashby", "a", True),
    ]


def test_real_registry_loads_under_schema_constraints(db):
    import_plan = feader.plan()

    _load(db, import_plan)

    assert db.execute("SELECT count(*) FROM company").fetchone()[0] == len(import_plan.companies)
    assert db.execute("SELECT count(*) FROM alias").fetchone()[0] == import_plan.alias_count
    assert db.execute("SELECT count(*) FROM token").fetchone()[0] == len(import_plan.tokens)
    assert db.execute("SELECT count(*) FROM token WHERE company_id IS NULL").fetchone()[0] == 0
    assert {token.company_id for token in import_plan.tokens} <= {
        company.id for company in import_plan.companies
    }


@pytest.mark.asyncio
async def test_feed_upserts_new_companies_aliases_then_tokens(monkeypatch):
    import_plan = feader.plan(
        (_company(id=2494, name="New Company", aliases=("new-company", "newco")),),
        (_entry(key="newco", company_id=2494, company_name="New Company"),),
    )
    monkeypatch.setattr(feader, "plan", lambda: import_plan)
    calls = []

    class Transaction:
        async def __aenter__(self):
            calls.append(("begin",))

        async def __aexit__(self, exc_type, exc, traceback):
            calls.append(("commit" if exc_type is None else "rollback",))

    class Connection:
        def transaction(self):
            return Transaction()

    async def record(kind, *args, **kwargs):
        calls.append((kind, args, kwargs))

    monkeypatch.setattr(
        store,
        "clear_registry_company_domains",
        lambda conn, company_ids: record("clear", conn, company_ids),
    )
    monkeypatch.setattr(
        store,
        "upsert_registry_company",
        lambda conn, **kwargs: record("company", conn, **kwargs),
    )
    monkeypatch.setattr(
        store,
        "upsert_registry_alias",
        lambda conn, **kwargs: record("alias", conn, **kwargs),
    )
    monkeypatch.setattr(
        store,
        "prune_registry_aliases",
        lambda conn, aliases: record("prune_aliases", conn, aliases),
    )
    monkeypatch.setattr(
        store,
        "align_company_identity",
        lambda conn: record("identity", conn),
    )
    monkeypatch.setattr(
        store,
        "upsert_registry_token",
        lambda conn, **kwargs: record("token", conn, **kwargs),
    )
    monkeypatch.setattr(
        store,
        "upsert_registry_feed",
        lambda conn, **kwargs: record("feed", conn, **kwargs),
    )
    monkeypatch.setattr(
        store,
        "reconcile_registry_inventory",
        lambda conn, identities: record("reconcile", conn, identities),
    )

    result = await feader.feed(Connection())

    assert result is import_plan
    assert [call[0] for call in calls] == [
        "begin",
        "clear",
        "company",
        "alias",
        "alias",
        "prune_aliases",
        "identity",
        "token",
        "feed",
        "reconcile",
        "commit",
    ]
    assert calls[2][2]["company_id"] == 2494
    assert [calls[3][2]["alias_norm"], calls[4][2]["alias_norm"]] == [
        "new-company",
        "newco",
    ]
    assert calls[7][2]["company_id"] == 2494
    assert calls[8][2]["enabled"] is True


@pytest.mark.asyncio
async def test_company_upsert_overrides_identity_and_sequence_is_aligned():
    calls = []

    class Connection:
        async def execute(self, query, *args):
            calls.append((query, args))

    conn = Connection()
    await store.upsert_registry_company(
        conn,
        company_id=2494,
        name="New Company",
        domain="new.example",
        domain_verified_at=None,
        description="",
    )
    await store.align_company_identity(conn)

    assert "OVERRIDING SYSTEM VALUE" in calls[0][0]
    assert "ON CONFLICT (id) DO UPDATE" in calls[0][0]
    assert calls[0][1][0] == 2494
    assert "pg_get_serial_sequence('company', 'id')" in calls[1][0]


@pytest.mark.asyncio
async def test_feed_seed_preserves_existing_feed_tuning():
    calls = []

    class Connection:
        async def fetchval(self, query, *args):
            calls.append((query, args))
            return 17

        async def execute(self, query, *args):
            calls.append((query, args))

    feed_id = await store.upsert_registry_feed(
        Connection(), source="greenhouse", key="acme", enabled=True
    )

    assert feed_id == 17
    # Seed-only: re-syncing must not overwrite the operator's inspector toggle.
    assert "ON CONFLICT (source, key) DO UPDATE SET source" in calls[0][0]
    assert "SET enabled" not in calls[0][0]
    assert "interval_s" not in calls[0][0]


@pytest.mark.asyncio
async def test_registry_reconciliation_disables_without_deleting_history():
    queries = []

    class Connection:
        async def execute(self, query, *args):
            queries.append(query)

    await store.reconcile_registry_inventory(
        Connection(), [{"source": "greenhouse", "key": "acme"}]
    )

    assert "UPDATE feed f SET enabled = false" in queries[0]
    assert "UPDATE token t SET status = 'inactive'" in queries[1]
    assert all("DELETE" not in query for query in queries)
