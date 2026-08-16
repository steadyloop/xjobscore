from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from xjobscore.inspector.cache import RunSnapshot
from xjobscore.inspector.db import CatalogDB, _id, feed_ref, registry_entries
from xjobscore.inspector.service import (
    HIDDEN_SOURCES,
    InspectorService,
    display_source,
    source_category,
)
from xjobscore.records import Feed, Item, Record
from xjobscore import sources
from xjobscore.sources import docxtract, json_api
from xjobscore.sources.registry.loader import companies

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


async def test_workday_reuses_prior_detail_path(
    monkeypatch, store_factory
):
    payload = {
        "jobPostingInfo": {
            "title": "Demo",
            "jobReqId": "R1",
            "externalUrl": "https://tenant.wd1.myworkdayjobs.com/Site/job/demo_R1",
            "jobDescription": "Full description",
            "startDate": "2026-08-01",
        }
    }
    captured = []

    async def harvest(name, feed, net, *, options=None):
        captured.append(options)
        item = (
            options.prior_items["/job/demo_R1"]
            if options.prior_items is not None
            else Item("R1", payload)
        )
        return Record(
            items=[item],
            http_status=200,
            reused_item_count=int(options.prior_items is not None),
            enumerated_item_count=1,
            partition_count=2,
            detail_fetched_count=int(options.prior_items is None),
            result_window_truncated=True,
        )

    monkeypatch.setattr(json_api, "harvest", harvest)
    feed = Feed(id=1, source="workday", key="tenant/wd1/Site", config={})

    async def run(label: str, *, seed: bool):
        store = await store_factory()
        if seed:
            now = datetime.now(timezone.utc)
            await store.save(
                RunSnapshot(
                    result_id=f"prior-{label}", feed_id=feed.id,
                    source=feed.source, key=feed.key, started_at=now, finished_at=now,
                    elapsed_ms=1, status="ok", http_status=200, item_count=1,
                    raw_size_bytes=1, truncated=False,
                    error_class=None, error_detail=None,
                    items=({"external_id": "R1", "kind": "json", "payload": payload,
                            "raw_document": {}},),
                    envelope={}, size_bytes=1,
                )
            )
        service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)
        snapshot = await service.fetch_once(feed.id)
        return service, snapshot

    warm_service, warm = await run("workday-warm", seed=True)
    _, cold = await run("workday-cold", seed=False)

    warm_options, cold_options = captured
    # As above: no width override, so a fetch here reproduces a production
    # poll. These used to pin 32/16 while the config said otherwise.
    assert warm_options.page_concurrency is None
    assert warm_options.detail_concurrency is None
    assert warm_options.list_revision_fields == ("title", "bulletFields.0")
    assert warm_options.detail_revision_fields == (
        "jobPostingInfo.title", "jobPostingInfo.jobReqId"
    )
    assert warm_options.prior_items["/job/demo_R1"].payload == payload
    # No prior payload staged at all: nothing to reuse.
    assert cold_options.prior_items is None
    # Workday no longer gets a bespoke per-tenant host semaphore here. Its
    # cross-fetch ceiling is Net's vendor cap, keyed by the myworkdayjobs.com
    # suffix so one ceiling spans every tenant hostname instead of handing
    # each its own allowance; that capping is covered in test_net.py, and is
    # created lazily on the first real request rather than eagerly here.
    assert "tenant.wd1.myworkdayjobs.com" not in warm_service._shared_host_semaphores
    assert warm.network["summary"] | {
        "reused_item_count": 1,
        "enumerated_count": 1,
        "partition_count": 2,
        "detail_fetched_count": 0,
        "result_window_truncated": True,
    } == warm.network["summary"]
    assert cold.network["summary"]["detail_fetched_count"] == 1


def test_live_registry_sources_are_registered_and_company_keys_are_configured():
    live = [entry for entry in registry_entries() if entry.verified]
    refs = [feed_ref(entry.source, entry.key) for entry in registry_entries()]

    assert {entry.source for entry in live} <= set(sources.SOURCES)
    assert len(refs) == len(set(refs))
    assert {"amazon", "apple"} <= set(JSON_API_CONFIGS)


def test_document_sources_are_declared_in_the_registry():
    declared_sources = set(sources.SOURCE_TYPES)

    assert set(DOCXTRACT_CONFIGS) - HIDDEN_SOURCES <= declared_sources


def test_single_feed_sources_are_embedded_in_the_registry():
    entries_by_source = {entry.source: entry for entry in registry_entries()}

    assert entries_by_source["amazon"].company_name == "Amazon"
    assert entries_by_source["flyio"].key == "flyio"
    assert entries_by_source["hn"].company_name == "Hacker News"
    assert entries_by_source["unstop"].company_name == "Unstop"


async def test_feed_rows_include_registered_sources_without_a_feed(monkeypatch, store):
    feed = Feed(id=1, source="inspector-test", key="demo", config={}, company_name="Demo Co")

    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setitem(sources.SOURCES, "unconfigured-inspector-test", harvest)
    service = InspectorService(FakeDB(feed), user_agent="test", result_store=store)

    rows = await service.feed_rows()
    by_source = {row["source"]: row for row in rows}

    assert by_source["inspector-test"]["configured"] is True
    assert by_source["inspector-test"]["company_name"] == "Demo Co"
    assert by_source["inspector-test"]["profile"] == {
        "adapter": "bespoke", "formats": [], "flavor": None, "archetypes": [],
    }
    assert by_source["unconfigured-inspector-test"] == {
        "feed_ref": None,
        "source": "unconfigured-inspector-test",
        "category": "Other",
        "profile": {"adapter": "bespoke", "formats": [], "flavor": None, "archetypes": []},
        "key": "",
        "company_id": None,
        "company_name": None,
        "company_description": None,
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
    pool = await store.pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {store.schema}.company(id, name, description)
            VALUES (42, 'Demo Co', 'Builds useful testing tools. (Oakland)')
            """
        )

    rows = await InspectorService(
        FakeDB(feed), user_agent="test", result_store=store
    ).feed_rows()
    row = next(row for row in rows if row["feed_ref"] == feed_ref(feed.source, feed.key))

    assert row["company_description"] == "Builds useful testing tools. (Oakland)"


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


async def test_registry_feed_is_required_for_endpoint_specific_sources(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"greenhouse": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {"greenhouse": {}})
    monkeypatch.setattr("xjobscore.inspector.db.registry_entries", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.registry_feeds", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {"body": {}})

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]
    assert feeds[0].config == {"body": {}}


async def test_registry_owned_json_api_source_has_no_synthetic_feed(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"amazon": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {"amazon": {}})
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})
    monkeypatch.setattr("xjobscore.inspector.db.registry_entries", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.registry_feeds", lambda: [])

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]


async def test_board_source_without_a_registry_feed_has_no_synthetic_feed(monkeypatch):
    async def harvest(_feed, _net):
        return Record()

    monkeypatch.setattr(sources, "SOURCES", {"jsonld": harvest, "workatastartup": harvest})
    monkeypatch.setattr("xjobscore.inspector.db.json_api.SPECS", {})
    monkeypatch.setattr("xjobscore.inspector.db.docxtract.SPECS", {"jsonld": {}})
    monkeypatch.setattr("xjobscore.inspector.db.registry_entries", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.registry_feeds", lambda: [])
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})

    feeds = await CatalogDB().all_feeds()

    assert [(feed.source, feed.key) for feed in feeds] == [("workatastartup", "workatastartup")]


async def test_registry_feed_refs_round_trip_without_numeric_ids(monkeypatch):
    monkeypatch.setattr("xjobscore.inspector.db.config.source_config", lambda source: {})
    database = CatalogDB()
    for feed in await database.all_feeds():
        ref = feed_ref(feed.source, feed.key)
        resolved = await database.get_feed_ref(ref)
        assert resolved is not None
        assert (resolved.source, resolved.key) == (feed.source, feed.key)

    assert await database.get_feed_ref("0" * 64) is None


def test_frontend_uses_public_feed_refs_and_sequential_group_fetch():
    static = Path(__file__).with_name("static")
    scripts = [
        static / "feeds.js",
        static / "feed" / "app.js",
        static / "shared" / "inspect.js",
        static / "scheduler" / "app.js",
        static / "postings" / "app.js",
        static / "postings-old" / "app.js",
    ]
    for script in scripts:
        assert "feed.id" not in script.read_text(), script.name

    feeds_js = (static / "feeds.js").read_text()
    assert "feed.last_result = await api(`/api/feeds/${feed.feed_ref}/fetch`" in feeds_js
    assert "for (const feed of feeds)" in feeds_js  # sequential by design


async def test_feed_rows_use_string_ids_and_stable_public_refs(store):
    feed = Feed(id=-7588981172000531025, source="amazon", key="amazon", config={})
    rows = await InspectorService(
        FakeDB(feed), user_agent="test", result_store=store
    ).feed_rows()
    row = next(row for row in rows if row["source"] == "amazon" and row["key"] == "amazon")

    assert "id" not in row
    assert row["feed_ref"] == feed_ref("amazon", "amazon")


def test_local_feed_ids_are_javascript_safe():
    assert abs(_id("workatastartup")) <= (1 << 53) - 1


async def test_inspector_initialization_imports_registry_companies(store):
    registry = companies()
    expected_aliases = {
        alias_norm
        for company in registry
        for alias_norm in company.aliases
    }

    assert await store.initialize() == len(registry)
    pool = await store.pool()
    async with pool.acquire() as conn:
        columns = {
            name: [
                row["column_name"]
                for row in await conn.fetch(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    ORDER BY ordinal_position
                    """,
                    store.schema,
                    name,
                )
            ]
            for name in ("company", "alias")
        }
        counts = (
            await conn.fetchval(f"SELECT count(*) FROM {store.schema}.company"),
            await conn.fetchval(f"SELECT count(*) FROM {store.schema}.alias"),
        )
        # Spot-check one arbitrary company against the registry projection so
        # data edits in companies.toml never break this structural test.
        probe = registry[0]
        probe_row = await conn.fetchrow(
            f"""
            SELECT a.company_id, c.domain, c.domain_verified_at, c.description
            FROM {store.schema}.company c
            JOIN {store.schema}.alias a ON a.company_id = c.id
            WHERE a.alias_norm = $1
            """,
            probe.aliases[0],
        )

    assert columns["company"] == ["id", "name", "domain", "domain_verified_at", "description"]
    assert columns["alias"] == ["alias_norm", "company_id", "alias"]
    assert counts == (len(registry), len(expected_aliases))
    assert tuple(probe_row) == (
        probe.id,
        None if probe.domain == "unknown" else probe.domain,
        probe.domain_verified_at if probe.verified else None,
        probe.desc,
    )

    # Re-importing is an upsert, not a duplicate seed.
    assert await store.initialize() == len(registry)
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            f"SELECT count(*) FROM {store.schema}.company"
        ) == len(registry)


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


def test_source_categories():
    assert source_category("lever") == "ATS"
    assert source_category("jobvite") == "ATS"
    assert source_category("teamtailor") == "ATS"
    for source in (
        "jazzhr", "manatal", "peopleadmin", "personio", "pinpoint",
        "recruitee", "successfactors", "talentreef", "ukg", "vincere",
    ):
        assert source_category(source) == "ATS"
    for source in ("amazon", "apple", "flyio", "janestreet"):
        assert source_category(source) == "Direct"
    for source in ("hn", "simplify", "unstop", "usajobs", "workatastartup"):
        assert source_category(source) == "Board"


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
