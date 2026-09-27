"""Policy layer: route behavior, PolicyStore degradation, store.py mutators."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xjobscore import store
from xjobscore.inspector.app import create_app
from xjobscore.inspector.db import feed_ref
from xjobscore.inspector.policy import (
    FeedNotInPostgres,
    PollNowRefused,
    PolicyStore,
    PolicyUnavailable,
)
from xjobscore.inspector.test_routes import FakeCatalog, FakePolicy
from xjobscore.records import Feed

REF = feed_ref("policy-test", "demo")


def make_client(monkeypatch, app_store, policy):
    app = create_app(
        catalog=FakeCatalog(Feed(id=1, source="policy-test", key="demo", config={})),
        store=app_store,
        user_agent="policy-test",
        policy=policy,
    )
    return TestClient(app)


# Routes ---------------------------------------------------------------------


def test_degraded_mode_disables_policy_writes(monkeypatch, app_store):
    with make_client(monkeypatch, app_store, DegradedPolicy()) as client:
        health = client.get("/api/health").json()
        assert health["postgres"]["connected"] is False

        rows = client.get("/api/feeds").json()["feeds"]
        assert all(row["policy"] is None for row in rows)

        response = client.post(f"/api/feeds/{REF}/policy", json={"enabled": False})
        assert response.status_code == 503

        assert client.post(f"/api/feeds/{REF}/import").status_code == 503


def test_policy_rows_merge_postgres_snapshot(monkeypatch, app_store):
    snapshot = {
        ("policy-test", "demo"): {
            "feed_id": 11,
            "enabled": True,
            "interval_s": 900,
            "strikes": 0,
            "due_at": None,
            "hold_until": None,
            "paused_at": None,
            "last_poll_at": None,
            "last_change_at": None,
            "has_etag": False,
        }
    }
    with make_client(monkeypatch, app_store, FakePolicy(snapshot)) as client:
        rows = client.get("/api/feeds").json()["feeds"]
        row = next(row for row in rows if row["feed_ref"] == REF)

        assert row["policy"]["in_postgres"] is True
        assert row["policy"]["interval_s"] == 900


def test_policy_mutations_are_forwarded(monkeypatch, app_store):
    policy = FakePolicy({})
    with make_client(monkeypatch, app_store, policy) as client:
        response = client.post(
            f"/api/feeds/{REF}/policy",
            json={"enabled": False, "interval_s": 3600, "paused": True},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False
        assert body["interval_s"] == 3600
        assert body["paused"] is True

        assert client.post(f"/api/feeds/{REF}/import", json={"enabled": True}).status_code == 200

    assert policy.calls == [
        ("set_enabled", "policy-test", "demo", False),
        ("set_interval", "policy-test", "demo", 3600),
        ("set_paused", "policy-test", "demo", True),
        ("import_feed", "policy-test", "demo", True),
    ]


def test_drop_feed_is_forwarded_without_a_token_mutation(monkeypatch, app_store):
    policy = FakePolicy({})
    with make_client(monkeypatch, app_store, policy) as client:
        response = client.delete(f"/api/feeds/{REF}")

    assert response.status_code == 200
    assert response.json() == {"feed_ref": REF, "feed_id": 11, "dropped": True}
    assert policy.calls == [("drop_feed", "policy-test", "demo")]


@pytest.mark.parametrize("interval_s", [60, 5400, 7 * 86_400])
def test_custom_interval_route_accepts_policy_range(monkeypatch, app_store, interval_s):
    policy = FakePolicy({})
    with make_client(monkeypatch, app_store, policy) as client:
        response = client.post(
            f"/api/feeds/{REF}/policy", json={"interval_s": interval_s}
        )
    assert response.status_code == 200
    assert response.json()["interval_s"] == interval_s


@pytest.mark.parametrize("interval_s", [59, 7 * 86_400 + 1])
def test_custom_interval_route_rejects_out_of_range(monkeypatch, app_store, interval_s):
    policy = FakePolicy({})
    with make_client(monkeypatch, app_store, policy) as client:
        response = client.post(
            f"/api/feeds/{REF}/policy", json={"interval_s": interval_s}
        )
    assert response.status_code == 400
    assert "interval_s must be within" in response.json()["detail"]


def test_feed_missing_from_postgres_maps_to_409(monkeypatch, app_store):
    with make_client(monkeypatch, app_store, AbsentFeedPolicy({})) as client:
        response = client.post(f"/api/feeds/{REF}/policy", json={"enabled": True})
        assert response.status_code == 409
        assert response.json()["detail"]["hint"] == "import"


def test_empty_policy_body_is_400_and_unknown_ref_404(monkeypatch, app_store):
    with make_client(monkeypatch, app_store, FakePolicy({})) as client:
        assert client.post(f"/api/feeds/{REF}/policy", json={}).status_code == 400
        assert (
            client.post(f"/api/feeds/{'0' * 64}/policy", json={"enabled": True}).status_code
            == 404
        )


class DegradedPolicy(FakePolicy):
    def __init__(self):
        super().__init__(None)

    async def set_enabled(self, source, key, enabled):
        raise PolicyUnavailable("no DSN configured")

    async def set_interval(self, source, key, interval_s):
        raise PolicyUnavailable("no DSN configured")

    async def import_feed(self, source, key, enabled=True):
        raise PolicyUnavailable("no DSN configured")


class AbsentFeedPolicy(FakePolicy):
    async def set_enabled(self, source, key, enabled):
        raise FeedNotInPostgres(f"{source}:{key}")


# PolicyStore ----------------------------------------------------------------


class FakeConnection:
    """Canned results keyed by SQL.

    ``match="contains"`` keys on a fragment instead of the whole statement, for
    the few queries that are a multi-line CTE the test has no interest in
    reproducing verbatim.
    """

    def __init__(self, results: dict[str, object], *, match: str = "exact"):
        self.results = results
        self.match = match
        self.queries: list[tuple[str, tuple]] = []

    def _record(self, sql: str, args: tuple) -> str:
        normalized = " ".join(sql.split())
        self.queries.append((normalized, args))
        return normalized

    def _lookup(self, sql: str, args: tuple, default=None):
        normalized = self._record(sql, args)
        if self.match == "exact":
            return self.results.get(normalized, default)
        for fragment, result in self.results.items():
            if fragment in normalized:
                return result
        return default

    async def fetchval(self, sql: str, *args):
        return self._lookup(sql, args)

    async def fetchrow(self, sql: str, *args):
        return self._lookup(sql, args)

    async def fetch(self, sql: str, *args):
        return self._lookup(sql, args, [])

    async def execute(self, sql: str, *args):
        return self._lookup(sql, args, "UPDATE 0")


class FakePool:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.closed = False

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool.connection

            async def __aexit__(self, *exc):
                return False

        return _Acquire()

    async def close(self):
        self.closed = True


async def test_policy_store_without_dsn_is_degraded():
    policy = PolicyStore(None)
    await policy.start()

    assert policy.status()["connected"] is False
    assert policy.status()["error"] == "no DSN configured"
    assert await policy.snapshot() is None
    assert await policy.company_alias_index() == {}
    assert await policy.company_descriptions({1}) == {}
    with pytest.raises(PolicyUnavailable):
        await policy.set_enabled("lever", "acme", True)


async def test_policy_store_connect_failure_is_degraded(monkeypatch):
    async def failing_connect(_dsn):
        raise OSError("connection refused")

    monkeypatch.setattr(store, "connect", failing_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")
    await policy.start()

    assert policy.status()["connected"] is False
    assert "connection refused" in policy.status()["error"]
    assert await policy.snapshot() is None


async def test_policy_store_reads_company_aliases_and_descriptions(monkeypatch):
    alias_sql = (
        "SELECT a.alias_norm, c.id AS company_id, c.name AS company_name "
        "FROM company_alias a JOIN company c ON c.id = a.company_id"
    )
    description_sql = (
        "SELECT id, description FROM company "
        "WHERE description IS NOT NULL AND description <> '' "
        "AND id = ANY($1::bigint[])"
    )
    connection = FakeConnection(
        {
            alias_sql: [
                {"alias_norm": "acme", "company_id": 7, "company_name": "Acme"}
            ],
            description_sql: [{"id": 7, "description": "Makes anvils."}],
        }
    )

    async def fake_connect(_dsn):
        return FakePool(connection)

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    assert await policy.company_alias_index() == {"acme": (7, "Acme")}
    assert await policy.company_descriptions({7, 99}) == {7: "Makes anvils."}
    assert connection.queries[-1][1] == ([7, 99],)


async def test_policy_store_set_enabled_uses_store_mutator(monkeypatch):
    connection = FakeConnection(
        {"UPDATE feed SET polling = $3 WHERE source = $1 AND key = $2 RETURNING id": 7}
    )
    pool = FakePool(connection)

    async def fake_connect(_dsn):
        return pool

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    result = await policy.set_enabled("lever", "acme", False)

    assert result == {"feed_id": 7, "enabled": False}
    assert connection.queries[0][1] == ("lever", "acme", False)


async def test_policy_store_missing_feed_raises(monkeypatch):
    pool = FakePool(FakeConnection({}))

    async def fake_connect(_dsn):
        return pool

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    with pytest.raises(FeedNotInPostgres):
        await policy.set_enabled("lever", "ghost", True)


async def test_policy_store_drop_feed_preserves_the_token(monkeypatch):
    connection = FakeConnection({"DELETE FROM feed": 7}, match="contains")
    pool = FakePool(connection)

    async def fake_connect(_dsn):
        return pool

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    assert await policy.drop_feed("lever", "acme") == {"feed_id": 7, "dropped": True}
    sql, args = connection.queries[0]
    assert "DELETE FROM feed" in sql
    # The `token` row survives the drop; only token_log is written, and only
    # to record that the feed left.
    assert "UPDATE token" not in sql
    assert "DELETE FROM token" not in sql
    assert "'feed_exited'" in sql
    assert args == ("lever", "acme")


async def test_policy_store_set_paused_writes_polling(monkeypatch):
    """Pause and disable are one column: holding a feed clears feed.polling."""
    connection = FakeConnection(
        {"UPDATE feed SET polling = $3 WHERE source = $1 AND key = $2 RETURNING id": 7}
    )
    pool = FakePool(connection)

    async def fake_connect(_dsn):
        return pool

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    assert await policy.set_paused("lever", "acme", True) == {
        "feed_id": 7,
        "paused": True,
        "enabled": False,
    }
    sql, args = connection.queries[0]
    assert "paused_at" not in sql
    assert args == ("lever", "acme", False)


async def test_policy_store_set_paused_on_a_missing_feed_raises(monkeypatch):
    pool = FakePool(FakeConnection({}))

    async def fake_connect(_dsn):
        return pool

    monkeypatch.setattr(store, "connect", fake_connect)
    policy = PolicyStore("postgresql://localhost/xjobscore")

    with pytest.raises(FeedNotInPostgres):
        await policy.set_paused("lever", "ghost", True)


# poll_now ------------------------------------------------------------------

_CONTROL_SQL = "SELECT running, changed_at, note FROM scheduler_control WHERE id"
_STATE_SQL = (
    "SELECT f.id, f.polling AS enabled, f.paused_at, f.hold_until > now() AS held FROM feed f "
    "WHERE f.source = $1 AND f.key = $2"
)
_EXPRESS_SQL = (
    "UPDATE feed SET due_at = now() - $2::interval "
    "WHERE id = $1 AND paused_at IS NULL RETURNING due_at"
)


def poll_now_store(monkeypatch, *, running=True, state=None, due_at=None):
    """A PolicyStore whose three poll_now queries answer as scripted."""
    results = {
        _CONTROL_SQL: {"running": running, "changed_at": None, "note": None},
    }
    if state is not None:
        results[_STATE_SQL] = state
    if due_at is not None:
        results[_EXPRESS_SQL] = due_at
    connection = FakeConnection(results)

    async def fake_connect(_dsn):
        return FakePool(connection)

    monkeypatch.setattr(store, "connect", fake_connect)
    return PolicyStore("postgresql://localhost/xjobscore"), connection


def healthy_state(**overrides):
    row = {"id": 7, "enabled": True, "paused_at": None, "held": False}
    row.update(overrides)
    return row


async def test_poll_now_backdates_due_at_past_the_backlog(monkeypatch):
    """due_at = now() would sort *behind* every overdue feed, not ahead of it.

    claim_due_feeds orders by due_at ascending, so the express write has to be
    older than anything real -- this asserts the interval parameter is what
    reaches the database, since that value is the whole fix.
    """
    when = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    policy, connection = poll_now_store(
        monkeypatch, state=healthy_state(), due_at=when
    )

    result = await policy.poll_now("lever", "acme")

    assert result == {"feed_id": 7, "due_at": when.isoformat(), "express": True}
    sql, args = connection.queries[-1]
    assert sql == _EXPRESS_SQL
    assert args[0] == 7
    assert args[1] >= timedelta(days=365), "backdate must clear a real backlog"


async def test_poll_now_refuses_while_the_scheduler_is_paused(monkeypatch):
    """The write would sit unclaimed until someone resumes -- a silent no-op."""
    policy, connection = poll_now_store(monkeypatch, running=False, state=healthy_state())

    with pytest.raises(PollNowRefused) as raised:
        await policy.poll_now("lever", "acme")

    assert raised.value.reason == "scheduler_paused"
    assert all(sql != _EXPRESS_SQL for sql, _ in connection.queries)


async def test_poll_now_refuses_a_disabled_feed(monkeypatch):
    """claim_due_feeds filters on f.polling, so this used to answer 200 and never poll."""
    policy, connection = poll_now_store(
        monkeypatch, state=healthy_state(enabled=False), due_at=datetime.now(timezone.utc)
    )

    with pytest.raises(PollNowRefused) as raised:
        await policy.poll_now("lever", "acme")

    assert raised.value.reason == "feed_disabled"
    assert all(sql != _EXPRESS_SQL for sql, _ in connection.queries)


async def test_poll_now_refuses_a_paused_feed(monkeypatch):
    policy, _ = poll_now_store(
        monkeypatch, state=healthy_state(paused_at=datetime.now(timezone.utc))
    )

    with pytest.raises(PollNowRefused) as raised:
        await policy.poll_now("lever", "acme")

    assert raised.value.reason == "feed_paused"


async def test_poll_now_refuses_a_feed_a_worker_already_holds(monkeypatch):
    """hold_until stays untouched: the lease belongs to whoever claimed it."""
    policy, connection = poll_now_store(monkeypatch, state=healthy_state(held=True))

    with pytest.raises(PollNowRefused) as raised:
        await policy.poll_now("lever", "acme")

    assert raised.value.reason == "already_polling"
    assert all(sql != _EXPRESS_SQL for sql, _ in connection.queries)


async def test_poll_now_on_a_missing_feed_raises(monkeypatch):
    policy, _ = poll_now_store(monkeypatch, state=None)
    with pytest.raises(FeedNotInPostgres):
        await policy.poll_now("lever", "ghost")


async def test_policy_store_interval_bounds():
    policy = PolicyStore("postgresql://localhost/xjobscore")

    with pytest.raises(ValueError):
        await policy.set_interval("lever", "acme", 5)
    with pytest.raises(ValueError):
        await policy.set_interval("lever", "acme", 10**9)


# store.py mutators ----------------------------------------------------------


async def test_store_set_feed_enabled_sql():
    connection = FakeConnection(
        {"UPDATE feed SET polling = $3 WHERE source = $1 AND key = $2 RETURNING id": 3}
    )

    assert await store.set_feed_enabled(connection, source="lever", key="acme", enabled=True) == 3


async def test_store_set_schedule_interval_sql():
    connection = FakeConnection(
        {"UPDATE feed SET interval_s = $2 WHERE id = $1": "UPDATE 1"}
    )

    assert await store.set_schedule_interval(connection, 3, 1800) is True
    assert connection.queries[0][1] == (3, 1800)


async def test_store_set_schedule_paused_sql():
    sql = (
        "UPDATE feed SET paused_at = CASE WHEN $2 THEN now() ELSE NULL END "
        "WHERE id = $1"
    )
    connection = FakeConnection({sql: "UPDATE 1"})

    assert await store.set_schedule_paused(connection, 3, True) is True
    assert connection.queries[0][1] == (3, True)
    # Releasing is the same statement with the flag flipped -- one column, one write.
    assert await store.set_schedule_paused(connection, 3, False) is True
    assert connection.queries[1][1] == (3, False)
