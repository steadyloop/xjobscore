from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xjobscore.inspector.cache import RunSnapshot
from xjobscore.inspector.db import CatalogDB, feed_ref
from xjobscore.inspector.service import (
    HIDDEN_SOURCES,
    InspectorService,
    display_source,
)
from xjobscore.records import Feed, Item, Posting, Record
from xjobscore import sources
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


async def test_fetch_persists_result_without_an_in_memory_cache(monkeypatch, store):
    feed = Feed(id=1, source="inspector-test", key="demo", config={})

    async def harvest(_feed, _net):
        return Record(
            items=[Item("job-1", {"title": "Demo"})],
            envelope={"company": "Acme"},
            http_status=200,
        )

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)

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


async def test_fetch_records_a_stage_breakdown_that_accounts_for_elapsed_ms(
    monkeypatch, store
):
    """A slow run has to be attributable without hand-decoding the network column."""
    feed = Feed(id=1, source="inspector-test", key="demo", config={})

    async def harvest(_feed, _net):
        return Record(items=[Item("job-1", {"title": "Demo"})], envelope={}, http_status=200)

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)

    snapshot = await service.fetch_once(feed.id)

    stages = snapshot.network["stages"]
    assert [stage["name"] for stage in stages] == ["harvest", "project", "serialize"]
    # The stages partition exactly the window elapsed_ms measures.
    assert sum(stage["elapsed_ms"] for stage in stages) <= snapshot.elapsed_ms + 1.0
    # Zero requests still reports the HTTP total, so "local" is computable.
    assert snapshot.network["summary"]["elapsed_ms"] == 0.0

    reloaded = await service.get_result(feed.id)
    assert [stage["name"] for stage in reloaded.network["stages"]] == [
        stage["name"] for stage in stages
    ]


async def test_failed_fetch_still_reports_where_the_time_went(monkeypatch, store):
    feed = Feed(id=1, source="inspector-test", key="demo", config={})

    async def harvest(_feed, _net):
        raise RuntimeError("board exploded")

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)

    snapshot = await service.fetch_once(feed.id)

    assert snapshot.status == "error"
    assert [stage["name"] for stage in snapshot.network["stages"]] == ["failed", "serialize"]


async def test_fetch_ref_resolves_to_the_exact_feed(monkeypatch, store):
    feed = Feed(id=1, source="inspector-test", key="demo", config={})

    async def harvest(_feed, _net):
        return Record(http_status=200)

    monkeypatch.setitem(sources.SOURCES, "inspector-test", harvest)
    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)

    snapshot = await service.fetch_once_ref(feed_ref(feed.source, feed.key))

    assert snapshot.feed_id == feed.id
    assert snapshot.key == feed.key


async def test_smartrecruiters_options_reuse_prior_items(
    monkeypatch, store_factory
):
    payload = {
        "id": "job-1",
        "jobAdId": "ad-1",
        "releasedDate": "2026-08-01T00:00:00Z",
        "name": "Demo",
        "postingUrl": "https://jobs.smartrecruiters.com/demo/job-1",
    }
    captured = []

    async def harvest(name, feed, net, *, options=None):
        captured.append((name, feed.etag, options))
        item = (
            options.prior_items["job-1"]
            if options.prior_items is not None
            else Item("job-1", payload)
        )
        return Record(
            items=[item],
            http_status=200,
            etag='W/"new"',
            reused_item_count=int(options.prior_items is not None),
        )

    monkeypatch.setattr(json_api, "harvest", harvest)
    feed = Feed(id=1, source="smartrecruiters", key="demo", config={})

    async def run(label: str, *, seed_status: str | None = "ok"):
        store = await store_factory()
        if seed_status is not None:
            now = datetime.now(timezone.utc)
            await store.save(
                RunSnapshot(
                    result_id=f"prior-{label}",
                    feed_id=feed.id,
                    source=feed.source,
                    key=feed.key,
                    started_at=now,
                    finished_at=now,
                    elapsed_ms=1,
                    status=seed_status,
                    http_status=200,
                    item_count=1,
                    raw_size_bytes=1,
                    truncated=seed_status == "partial",
                    error_class=None,
                    error_detail=None,
                    items=(
                        {
                            "external_id": "job-1",
                            "kind": "json",
                            "payload": payload,
                            "raw_document": {},
                        },
                    ),
                    envelope={},
                    size_bytes=1,
                    response_etag='W/"prior"',
                )
            )
        service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)
        return await service.fetch_once(feed.id)

    warm = await run("warm")
    cold = await run("cold", seed_status=None)
    partial = await run("partial", seed_status="partial")

    warm_name, warm_etag, warm_options = captured[0]
    assert warm_name == "smartrecruiters"
    assert warm_etag == 'W/"prior"'
    assert warm_options.page_step == 100
    # Widths stay None so the config governs: the inspector must measure the
    # width production actually runs, not one of its own.
    assert warm_options.page_concurrency is None
    assert warm_options.detail_concurrency is None
    assert warm_options.revision_fields == ("jobAdId", "releasedDate")
    assert warm_options.prior_items["job-1"].payload == payload
    assert warm.network["summary"]["reused_item_count"] == 1

    # No prior payload staged at all: nothing to reuse, no validator to send.
    _, cold_etag, cold_options = captured[1]
    assert cold_etag is None
    assert cold_options.prior_items is None
    assert cold.network["summary"]["reused_item_count"] == 0

    _, partial_etag, partial_options = captured[2]
    assert partial_etag is None
    # A partial prefix may still save matching detail requests after the full
    # list is fetched; it just cannot authorize a whole-list 304.
    assert partial_options.prior_items["job-1"].payload == payload
    assert partial.network["summary"]["reused_item_count"] == 1


async def test_catalog_without_postgres_has_no_fallback():
    assert await CatalogDB().all_feeds() == []


async def test_feed_rows_hide_jsonld(monkeypatch, store):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setitem(sources.SOURCES, "jsonld", harvest)
    service = InspectorService(
        FakeDB(Feed(id=1, source="jsonld", key="https://example.com", config={})),
        user_agent="test",
        result_store=store,
    )

    rows = await service.feed_rows()

    assert all(row["source"] != "jsonld" for row in rows)


async def test_feed_rows_resolve_company_description_by_id(store):
    feed = Feed(
        id=1,
        source="inspector-test",
        key="demo",
        config={},
        company_id=42,
        company_name="Demo Co",
    )
    class CompanyStore:
        async def company_descriptions(self, company_ids):
            assert company_ids == {42}
            return {42: "Builds useful testing tools. (Oakland)"}

    rows = await InspectorService(
        FakeDB(feed),
        user_agent="test",
        result_store=store,
        company_store=CompanyStore(),
    ).feed_rows()
    row = next(row for row in rows if row["feed_ref"] == feed_ref(feed.source, feed.key))

    assert row["company_description"] == "Builds useful testing tools. (Oakland)"


async def test_parse_preview_resolves_company_from_postgres_alias_snapshot(
    monkeypatch, store
):
    feed = Feed(id=1, source="alias-test", key="demo", type="Board")
    now = datetime.now(timezone.utc)
    await store.save(
        RunSnapshot(
            result_id="alias-preview",
            feed_id=feed.id,
            source=feed.source,
            key=feed.key,
            started_at=now,
            finished_at=now,
            elapsed_ms=1,
            status="ok",
            http_status=200,
            item_count=1,
            raw_size_bytes=1,
            truncated=False,
            error_class=None,
            error_detail=None,
            items=(
                {
                    "external_id": "job-1",
                    "kind": "json",
                    "payload": {},
                    "raw_document": {},
                },
            ),
            envelope={},
            size_bytes=1,
        )
    )

    def parse(_feed, item, _envelope):
        return [
            Posting(
                external_id=item.external_id,
                title="Engineer",
                url_posting="https://example.test/job-1",
                company_raw="Acme, Inc.",
            )
        ]

    class CompanyStore:
        async def company_alias_index(self):
            return {"acme": (42, "Acme")}

    monkeypatch.setitem(sources.PARSERS, feed.source, parse)
    result = await InspectorService(
        FakeDB(feed),
        user_agent="test",
        result_store=store,
        company_store=CompanyStore(),
    ).get_result(feed.id)

    assert result is not None
    assert result.parse_summary["resolved"] == 1
    resolution = result.parse_items[0]["canonicals"][0]["company_resolution"]
    assert resolution == {
        "status": "resolved",
        "company_id": 42,
        "company_name": "Acme",
        "company_raw": "Acme, Inc.",
    }


async def test_feed_rows_and_result_view_read_postgres_after_a_service_restart(store):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, company_name="Demo Co")
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="persisted", feed_id=feed.id, source=feed.source, key=feed.key,
        started_at=now, finished_at=now, elapsed_ms=12.5, status="ok", http_status=200,
        item_count=1, raw_size_bytes=18, truncated=False,
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


def test_frontend_uses_public_feed_refs_and_sequential_group_fetch():
    static = Path(__file__).with_name("static")
    scripts = [
        static / "feeds.js",
        static / "feed" / "app.js",
        static / "shared" / "inspect.js",
        static / "scheduler" / "app.js",
        static / "postings" / "app.js",
    ]
    for script in scripts:
        assert "feed.id" not in script.read_text(), script.name

    feeds_js = (static / "feeds.js").read_text()
    assert "feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`" in feeds_js
    assert "for (const feed of feeds)" in feeds_js  # sequential by design
    # Header Enable-all has the same import-and-enable semantics as row/vendor
    # toggles; it must not discard catalog-only feeds before the bulk request.
    candidates = feeds_js.split("function enableCandidates()", 1)[1].split("}\n", 1)[0]
    assert "feed.policy?.in_postgres" not in candidates
    assert "!(feed.policy.in_postgres && feed.policy.enabled)" in candidates
    assert "await setFeedsEnabled(feeds, true)" in feeds_js


async def test_feed_rows_use_string_ids_and_stable_public_refs(store):
    feed = Feed(id=-7588981172000531025, source="amazon", key="amazon", config={})
    rows = await InspectorService(
        FakeDB(feed), user_agent="test", result_store=store
    ).feed_rows()
    row = next(row for row in rows if row["source"] == "amazon" and row["key"] == "amazon")

    assert "id" not in row
    assert row["feed_ref"] == feed_ref("amazon", "amazon")


async def test_result_store_round_trips_items_and_envelope(store):
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="persisted", feed_id=3, source="lever", key="acme",
        started_at=now, finished_at=now, elapsed_ms=12.5, status="ok", http_status=200,
        item_count=1, raw_size_bytes=18, truncated=False,
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
    await store.save(snapshot)
    loaded = await store.load()
    assert len(loaded) == 1
    assert loaded[0].summary == snapshot.summary
    assert loaded[0].items == snapshot.items
    assert loaded[0].envelope == snapshot.envelope
    assert loaded[0].item_summary(0)["display_name"] == "Demo"


def test_item_picker_uses_job_title_and_hides_identifier_noise():
    now = datetime.now(timezone.utc)
    snapshot = RunSnapshot(
        result_id="title", feed_id=1, source="teamtailor", key="acme",
        started_at=now, finished_at=now, elapsed_ms=1, status="ok", http_status=200,
        item_count=2, raw_size_bytes=0, truncated=False,
        error_class=None, error_detail=None,
        items=(
            {"external_id": "opaque-id", "kind": "json", "payload": {"jobTitle": "Platform Engineer"}, "raw_document": {}},
            {"external_id": "opaque-id-2", "kind": "json", "payload": {"postingTitle": "Business Expert"}, "raw_document": {}},
        ),
        envelope={}, size_bytes=1,
    )

    assert snapshot.item_summary(0)["display_name"] == "Platform Engineer"
    assert snapshot.item_summary(1)["display_name"] == "Business Expert"
