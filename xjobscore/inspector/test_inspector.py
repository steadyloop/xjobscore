from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xjobscore.inspector.cache import RunSnapshot
from xjobscore.inspector.db import CatalogDB, _id
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.service import InspectorService, display_source, source_category
from xjobscore.records import Feed, Item, Record
from xjobscore import sources
from xjobscore.sources.catalog import entries, feed_ref
from xjobscore.sources import docxtract, json_api

DOCXTRACT_CONFIGS = docxtract.CONFIGS
JSON_API_CONFIGS = json_api.CONFIGS


class FakeDB:
    def __init__(self, feed: Feed):
        self.feed = feed

    async def get_feed(self, feed_id: int):
        return self.feed if feed_id == self.feed.id else None

    async def all_feeds(self):
        return [self.feed]


async def test_fetch_persists_result_without_an_in_memory_cache(monkeypatch, tmp_path):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, state={})

    async def harvest(_feed, _net):
        return Record(
            items=[Item("job-1", {"title": "Demo"})],
            envelope={"company": "Acme"},
            http_status=200,
        )

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(
        FakeDB(feed), user_agent="test", result_store=ResultStore(tmp_path / "inspector.sqlite3")
    )

    snapshot = await service.fetch_once(feed.id)

    assert snapshot.status == "ok"
    assert snapshot.item_count == 1
    assert snapshot.raw_size_bytes > 0
    assert snapshot.envelope == {"company": "Acme"}
    assert snapshot.item_detail(0)["raw_document"]["payload_column"] == "payload_json"
    assert not hasattr(service, "cache")
    stored = await service.get_result(feed.id)
    assert stored is not None
    assert stored.result_id == snapshot.result_id


async def test_fetch_ref_resolves_to_the_exact_feed(monkeypatch, tmp_path):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, state={})

    async def harvest(_feed, _net):
        return Record(http_status=200)

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(
        FakeDB(feed), user_agent="test", result_store=ResultStore(tmp_path / "inspector.sqlite3")
    )

    snapshot = await service.fetch_once_ref(feed_ref(feed.source, feed.key))

    assert snapshot.feed_id == feed.id
    assert snapshot.key == feed.key


def test_live_catalog_sources_are_registered_and_company_keys_are_configured():
    live = [entry for entry in entries() if entry.status == "live"]
    refs = [feed_ref(entry.source, entry.key) for entry in entries()]

    assert {entry.source for entry in live} <= set(sources.SOURCES)
    assert len(refs) == len(set(refs))
    assert {"amazon", "apple", "tesla", "uber"} <= set(JSON_API_CONFIGS)


def test_webboard_sources_are_catalog_backed():
    live_sources = {entry.source for entry in entries() if entry.status == "live"}

    assert set(DOCXTRACT_CONFIGS) <= live_sources


async def test_feed_rows_include_registered_sources_without_a_feed(monkeypatch, tmp_path):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, state={}, company_name="Demo Co")

    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setitem(sources.SOURCES, "unconfigured-inspector-test", harvest)
    service = InspectorService(
        FakeDB(feed), user_agent="test", result_store=ResultStore(tmp_path / "inspector.sqlite3")
    )

    rows = await service.feed_rows()
    by_source = {row["source"]: row for row in rows}

    assert by_source["inspector-test"]["configured"] is True
    assert by_source["inspector-test"]["company_name"] == "Demo Co"
    assert by_source["unconfigured-inspector-test"] == {
        "feed_ref": None,
        "source": "unconfigured-inspector-test",
        "category": "Other",
        "key": "",
        "company_id": None,
        "company_name": None,
        "configured": False,
        "enabled": False,
        "complete": False,
        "closes": False,
        "credentialed": False,
        "cadence_s": None,
        "next_poll_at": None,
        "source_registered": True,
        "parser_registered": False,
        "last_result": None,
    }


async def test_feed_rows_and_result_view_read_sqlite_after_a_service_restart(tmp_path):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, state={}, company_name="Demo Co")
    store = ResultStore(tmp_path / "inspector.sqlite3")
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="persisted", feed_id=feed.id, source=feed.source, key=feed.key,
        started_at=now, finished_at=now, elapsed_ms=12.5, status="ok", http_status=200,
        item_count=1, raw_size_bytes=18, truncated=False, force_full=True,
        error_class=None, error_detail=None,
        items=(
            {"external_id": "job-1", "kind": "json", "payload": {"title": "Demo"}, "raw_document": {}},
        ),
        envelope={"company": "Demo Co"}, size_bytes=100,
    )
    await store.save(snapshot)

    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)
    rows = await service.feed_rows()
    result = await service.get_result(feed.id)
    row = next(row for row in rows if row["feed_ref"] == feed_ref(feed.source, feed.key))

    assert row["last_result"]["result_id"] == "persisted"
    assert row["last_result"]["item_count"] == 1
    assert result is not None
    assert result.items == snapshot.items


async def test_catalog_is_required_for_endpoint_specific_sources(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"greenhouse": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {"greenhouse": {}})
    monkeypatch.setattr("xjobscore.inspector.db.catalog_feeds", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {"body": {}})

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]
    assert feeds[0].config == {"body": {}}


async def test_catalog_owned_json_api_source_has_no_synthetic_feed(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"amazon": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {"amazon": {}})
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})
    monkeypatch.setattr("xjobscore.inspector.db.catalog_feeds", lambda: [])

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]


async def test_webboard_source_without_a_catalog_entry_has_no_synthetic_feed(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"jsonld": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {})
    monkeypatch.setattr("xjobscore.inspector.db.docxtract.SPECS", {"jsonld": {}})
    monkeypatch.setattr("xjobscore.inspector.db.catalog_entries", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.catalog_feeds", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]


async def test_catalog_feed_refs_round_trip_without_numeric_ids(monkeypatch):
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})
    database = CatalogDB()
    for feed in await database.all_feeds():
        ref = feed_ref(feed.source, feed.key)
        resolved = await database.get_feed_ref(ref)
        assert resolved is not None
        assert (resolved.source, resolved.key) == (feed.source, feed.key)

    assert await database.get_feed_ref("0" * 64) is None


def test_frontend_group_fetch_uses_public_feed_refs():
    script = Path(__file__).with_name("static") / "app.js"
    text = script.read_text()

    assert "feed.id" not in text
    assert "api(`/api/feeds/${feed.feed_ref}/fetch`" in text
    assert "for (const feed of feeds)" in text
    assert "feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`" in text
    assert "state.groupFetch.completed += 1" in text
    assert "state.allFetch.completed += 1" in text


async def test_feed_rows_use_string_ids_and_stable_public_refs(tmp_path):
    feed = Feed(id=-7588981172000531025, source="amazon", key="amazon", config={}, state={})
    rows = await InspectorService(
        FakeDB(feed), user_agent="test", result_store=ResultStore(tmp_path / "inspector.sqlite3")
    ).feed_rows()
    row = next(row for row in rows if row["source"] == "amazon" and row["key"] == "amazon")

    assert "id" not in row
    assert row["feed_ref"] == feed_ref("amazon", "amazon")


def test_local_feed_ids_are_javascript_safe():
    assert abs(_id("workatastartup")) <= (1 << 53) - 1


async def test_result_store_round_trips_items_and_envelope(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="persisted", feed_id=3, source="lever", key="acme",
        started_at=now, finished_at=now, elapsed_ms=12.5, status="ok", http_status=200,
        item_count=1, raw_size_bytes=18, truncated=False, force_full=True,
        error_class=None, error_detail=None,
        items=(
            {
                "external_id": "job-1",
                "kind": "json",
                "payload": {"title": "Demo"},
                "raw_document": {"content_hash": "abc"},
            },
        ),
        envelope={"company": "Acme"}, size_bytes=100,
    )
    store = ResultStore(tmp_path / "inspector.sqlite3")
    await store.save(snapshot)
    loaded = await store.load()
    assert len(loaded) == 1
    assert loaded[0].summary == snapshot.summary
    assert loaded[0].items == snapshot.items
    assert loaded[0].envelope == snapshot.envelope
    assert loaded[0].item_summary(0)["display_name"] == "Demo"


def test_source_categories():
    assert source_category("lever") == "ATS"
    assert source_category("jobvite") == "ATS"
    assert source_category("teamtailor") == "ATS"
    assert source_category("amazon") == "ATS"
    assert source_category("janestreet") == "Web Board"
    assert source_category("workatastartup") == "Web Board"
    assert source_category("simplify") == "Web Board"


def test_item_picker_uses_job_title_and_hides_identifier_noise():
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="title", feed_id=1, source="teamtailor", key="acme",
        started_at=now, finished_at=now, elapsed_ms=1, status="ok", http_status=200,
        item_count=2, raw_size_bytes=0, truncated=False, force_full=True,
        error_class=None, error_detail=None,
        items=(
            {"external_id": "opaque-id", "kind": "json", "payload": {"jobTitle": "Platform Engineer"}, "raw_document": {}},
            {"external_id": "opaque-id-2", "kind": "json", "payload": {"postingTitle": "Business Expert"}, "raw_document": {}},
        ),
        envelope={}, size_bytes=1,
    )

    assert snapshot.item_summary(0)["display_name"] == "Platform Engineer"
    assert snapshot.item_summary(1)["display_name"] == "Business Expert"
