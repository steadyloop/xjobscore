from __future__ import annotations

import asyncio
import sqlite3

import pytest

from xjobscore.records import Feed, Item, Record

from oneshot_ui.db import Database
from oneshot_ui.gateway import AdapterGateway, GatewayResult
from oneshot_ui.scheduler import ManualFetchCoordinator


def make_feed(feed_id: int = -1, source: str = "fake") -> Feed:
    return Feed(
        id=feed_id,
        source=source,
        key=f"board-{abs(feed_id)}",
        config={},
        state={},
        company_id=f"company-{abs(feed_id)}",
        company_name="Example",
    )


async def enable_feeds(database: Database, feeds: list[Feed]) -> None:
    for feed in feeds:
        await database.execute(
            """
            INSERT INTO source_selections(feed_id, enabled, updated_at)
            VALUES (?, 1, 'now')
            ON CONFLICT(feed_id) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (feed.id,),
        )


class FakeGateway:
    def __init__(self, feeds: list[Feed], *, delay: float = 0) -> None:
        self.feeds = feeds
        self.delay = delay
        self.fetch_calls: list[int] = []
        self.connected = False
        self.active = 0
        self.peak_active = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def all_feeds(self) -> list[Feed]:
        return list(self.feeds)

    async def get_feed(self, feed_id: int) -> Feed | None:
        return next((feed for feed in self.feeds if feed.id == feed_id), None)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return None

    async def fetch(self, feed: Feed) -> GatewayResult:
        self.fetch_calls.append(feed.id)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            record = Record(
                items=[Item(external_id=str(feed.id), payload={"title": "Engineer"})],
                envelope={"company": "Example"},
                http_status=200,
                validators={"etag": f'"{abs(feed.id)}"'},
            )
            return GatewayResult(feed, record, (), "available")
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_initialization_applies_full_schema_in_wal_without_fetching(tmp_path) -> None:
    feed = make_feed()
    gateway = FakeGateway([feed])
    database = Database(tmp_path / "oneshot.sqlite")
    coordinator = ManualFetchCoordinator(database, gateway)  # type: ignore[arg-type]

    await coordinator.initialize()
    await coordinator.initialize()

    assert gateway.fetch_calls == []
    expected = {
        "feeds",
        "polls",
        "observations",
        "revisions",
        "identifiers",
        "entities",
        "memberships",
        "evidence",
        "candidates",
        "decisions",
        "requirements",
        "classification",
        "scores",
        "triage",
        "applications",
        "events",
        "profile",
        "settings",
        "source_selections",
    }
    rows = await database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert expected <= {row["name"] for row in rows}
    assert (await database.fetch_one("PRAGMA journal_mode"))["journal_mode"] == "wal"
    assert (await database.fetch_one("SELECT count(*) AS n FROM feeds"))["n"] == 1

    await coordinator.close()


@pytest.mark.asyncio
async def test_only_explicit_trigger_harvests_and_persists_raw_result(tmp_path) -> None:
    feed = make_feed()
    gateway = FakeGateway([feed])
    database = Database(tmp_path / "oneshot.sqlite")
    ingested: list[GatewayResult] = []
    coordinator = ManualFetchCoordinator(
        database,
        gateway,  # type: ignore[arg-type]
        on_ingest=ingested.append,
    )
    await coordinator.initialize()
    await enable_feeds(database, [feed])

    result = await coordinator.trigger(feed.id)

    assert gateway.fetch_calls == [feed.id]
    assert result.outcome == "ok"
    assert result.health == "healthy"
    assert len(ingested) == 1
    stored_feed = await database.fetch_one(
        "SELECT health, parser_state, state_json, last_poll_id FROM feeds WHERE id = ?",
        (feed.id,),
    )
    assert stored_feed is not None
    assert stored_feed["health"] == "healthy"
    assert stored_feed["parser_state"] == "available"
    assert '"etag":"\\"1\\""' in stored_feed["state_json"]
    assert stored_feed["last_poll_id"] == result.poll_id
    observation = await database.fetch_one(
        "SELECT external_id, payload_json FROM observations WHERE poll_id = ?",
        (result.poll_id,),
    )
    assert observation == {"external_id": "-1", "payload_json": '{"title":"Engineer"}'}

    await coordinator.close()


@pytest.mark.asyncio
async def test_missing_parser_is_a_visible_success_state(tmp_path) -> None:
    feed = make_feed()
    gateway = FakeGateway([feed])

    async def missing_parser_fetch(selected: Feed) -> GatewayResult:
        gateway.fetch_calls.append(selected.id)
        return GatewayResult(
            selected,
            Record(items=[], http_status=304, validators={"etag": '"same"'}),
            (),
            "missing",
            "parser is not registered",
        )

    gateway.fetch = missing_parser_fetch  # type: ignore[method-assign]
    database = Database(tmp_path / "oneshot.sqlite")
    coordinator = ManualFetchCoordinator(database, gateway)  # type: ignore[arg-type]
    await coordinator.initialize()
    await enable_feeds(database, [feed])

    result = await coordinator.trigger(feed)

    assert result.outcome == "not_modified"
    assert result.health == "missing_parser"
    assert result.parser_state == "missing"
    row = await database.fetch_one("SELECT health, last_error FROM feeds WHERE id = ?", (feed.id,))
    assert row == {"health": "missing_parser", "last_error": "parser is not registered"}
    await coordinator.close()


@pytest.mark.asyncio
async def test_harvest_error_is_returned_and_persisted(tmp_path) -> None:
    feed = make_feed()
    gateway = FakeGateway([feed])

    async def failed_fetch(selected: Feed) -> GatewayResult:
        gateway.fetch_calls.append(selected.id)
        raise RuntimeError("vendor unavailable")

    gateway.fetch = failed_fetch  # type: ignore[method-assign]
    database = Database(tmp_path / "oneshot.sqlite")
    coordinator = ManualFetchCoordinator(database, gateway)  # type: ignore[arg-type]
    await coordinator.initialize()
    await enable_feeds(database, [feed])

    result = await coordinator.trigger(feed)

    assert result.outcome == "error"
    assert result.health == "error"
    assert result.error == "RuntimeError: vendor unavailable"
    row = await database.fetch_one(
        "SELECT health, last_error, state_json FROM feeds WHERE id = ?", (feed.id,)
    )
    assert row is not None
    assert row["health"] == "error"
    assert row["last_error"] == "RuntimeError: vendor unavailable"
    assert '"consecutive_failures":1' in row["state_json"]
    await coordinator.close()


@pytest.mark.asyncio
async def test_explicit_list_is_concurrency_bounded_and_limited(tmp_path) -> None:
    feeds = [make_feed(-index) for index in range(1, 7)]
    gateway = FakeGateway(feeds, delay=0.02)
    database = Database(tmp_path / "oneshot.sqlite")
    coordinator = ManualFetchCoordinator(
        database,
        gateway,  # type: ignore[arg-type]
        max_concurrency=2,
        max_selection=5,
    )
    await coordinator.initialize()
    await enable_feeds(database, feeds)

    results = await coordinator.trigger_many(feeds[:5])

    assert [result.feed.id for result in results] == [feed.id for feed in feeds[:5]]
    assert sorted(gateway.fetch_calls) == sorted(feed.id for feed in feeds[:5])
    assert gateway.peak_active == 2
    with pytest.raises(ValueError, match="at most 5"):
        await coordinator.trigger_many(feeds)
    assert sorted(gateway.fetch_calls) == sorted(feed.id for feed in feeds[:5])
    await coordinator.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back_all_statements(tmp_path) -> None:
    database = Database(tmp_path / "oneshot.sqlite")
    await database.initialize()

    def broken(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES ('one', '1', 'now')"
        )
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        await database.transaction(broken)
    assert await database.fetch_all("SELECT * FROM settings") == []
    await database.close()


@pytest.mark.asyncio
async def test_catalog_mirror_disables_removed_feeds_without_affecting_single_upsert(tmp_path) -> None:
    database = Database(tmp_path / "oneshot.sqlite")
    first, second = make_feed(-31), make_feed(-32)
    await database.upsert_feeds([first, second])
    await database.upsert_feeds([first])
    assert await database.fetch_one(
        "SELECT enabled FROM feeds WHERE id = ?", (second.id,)
    ) == {"enabled": 0}

    # Resolving/fetching one current feed must not disable its catalog peers.
    await database.upsert_feeds([first, second])
    await database.upsert_feed(first)
    assert await database.fetch_one(
        "SELECT enabled FROM feeds WHERE id = ?", (second.id,)
    ) == {"enabled": 1}
    await database.close()


@pytest.mark.asyncio
async def test_gateway_uses_one_shared_net_and_registry(monkeypatch) -> None:
    feed = make_feed(source="registered")

    class Catalog:
        async def connect(self) -> None:
            pass

        async def disconnect(self) -> None:
            pass

        async def all_feeds(self) -> list[Feed]:
            return [feed]

    class FakeNet:
        enters = 0

        async def __aenter__(self):
            self.enters += 1
            return self

        async def __aexit__(self, *exc: object) -> None:
            pass

    seen_nets: list[FakeNet] = []

    async def harvest(selected: Feed, net: FakeNet) -> Record:
        seen_nets.append(net)
        return Record(items=[Item("one", {"x": 1})], http_status=200)

    monkeypatch.setattr("oneshot_ui.gateway.sources.get", lambda name: harvest)
    monkeypatch.setattr("oneshot_ui.gateway.sources.parser", lambda name: (lambda f, i, e: []))
    net = FakeNet()
    gateway = AdapterGateway(catalog=Catalog(), net=net)  # type: ignore[arg-type]

    await gateway.connect()
    await gateway.connect()
    await gateway.fetch(feed)
    await gateway.fetch(feed)
    await gateway.disconnect()

    assert net.enters == 1
    assert seen_nets == [net, net]
