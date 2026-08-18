from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from xjobscore import router, run, silver, store, work
from xjobscore.records import Fact, Feed, Item, Label, Posting, Record, Target


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--enrich"], "all"),
        (["--enrich", "job_type"], "job_type"),
        (["--enrich", "skills"], "skills"),
        (["--enrich", "degrees"], "degrees"),
        (["--enrich", "dedup"], "dedup"),
    ],
)
def test_enrich_cli_selects_all_or_one(arguments, expected):
    config = run.load_config(["--dsn", "postgresql://example/test", *arguments])

    assert config.enrich == expected


@pytest.mark.parametrize("legacy", ["--gold", "--gold-step"])
def test_legacy_gold_cli_flags_are_rejected(legacy):
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", legacy])


def test_seed_cli_selects_standalone_registry_seed():
    config = run.load_config(["--dsn", "postgresql://example/test", "--seed"])

    assert config.seed is True


def test_fetch_all_cli_selects_full_sweep_and_concurrency():
    config = run.load_config(
        ["--dsn", "postgresql://example/test", "--fetch-all", "--concurrency", "3"]
    )

    assert config.fetch_all is True
    assert config.concurrency == 3


def test_fetch_all_cli_rejects_zero_concurrency():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--concurrency", "0"])


def test_sync_registry_cli_flag_is_rejected():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--sync-registry"])


def test_schema_indexes_the_opportunities_filter_and_default_sort():
    schema = (Path(__file__).parent.parent / "schema.sql").read_text()

    assert "job_posting_opportunity_type_active_idx" in schema
    assert "((attributes ->> 'opportunity_type'))" in schema
    assert "'internship'" in schema
    assert "'coop'" in schema
    assert "job_posting_fresh_active_idx" in schema
    assert "((COALESCE(posted_at, first_seen_at)) DESC, id DESC)" in schema


def test_schema_opportunity_types_match_the_registry():
    root = Path(__file__).parent.parent
    schema = (root / "schema.sql").read_text()
    registry = yaml.safe_load(
        (root / "xjobscore" / "registry" / "type.yaml").read_text()
    )
    constraint = re.search(
        r"attributes ->> 'opportunity_type' IN \((.*?)\)", schema, re.DOTALL
    )

    assert constraint is not None
    schema_types = set(re.findall(r"'([^']+)'", constraint.group(1)))
    registry_types = {rule["type"] for rule in registry["type"]}
    assert schema_types == registry_types | {"unclassified"}


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, *args):
        return "INSERT 0 1"


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_fetch_all_dry_run_fetches_every_eligible_feed_with_bounded_concurrency(monkeypatch):
    feeds = [
        Feed(id=index, source="fixture", key=f"feed-{index}") for index in range(1, 4)
    ]
    active = 0
    peak = 0
    fetched = []

    async def candidates(conn, now):
        return feeds

    async def harvest(feed, net):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        fetched.append(feed.key)
        active -= 1

    monkeypatch.setattr(store, "fetchable_feeds", candidates)
    monkeypatch.setattr(run, "_registry_index", lambda: {})
    monkeypatch.setattr(run, "source_types", lambda: {})
    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    cfg = run.Config(
        dsn="postgresql://example/test",
        user_agent="test",
        dry_run=True,
        fetch_all=True,
        concurrency=2,
    )

    result = await run.fetch_all(_Pool(), object(), cfg)

    assert set(fetched) == {"feed-1", "feed-2", "feed-3"}
    assert peak == 2
    assert result == run.FetchAllResult(eligible=3, polled=3)


@pytest.mark.asyncio
async def test_fetch_all_claims_each_feed_and_reports_concurrent_skips(monkeypatch):
    feeds = [
        Feed(id=index, source="fixture", key=f"feed-{index}") for index in range(1, 4)
    ]
    claimed = []

    async def candidates(conn, now):
        return feeds

    async def claim(conn, *, source, key, now, lease_seconds):
        claimed.append((source, key, lease_seconds))
        return None if key == "feed-2" else next(feed for feed in feeds if feed.key == key)

    async def poll(pool, net, feed, now):
        return "partial" if feed.key == "feed-3" else "ok"

    monkeypatch.setattr(store, "fetchable_feeds", candidates)
    monkeypatch.setattr(store, "claim_feed", claim)
    monkeypatch.setattr(run, "poll", poll)
    monkeypatch.setattr(run, "_registry_index", lambda: {})
    monkeypatch.setattr(run, "source_types", lambda: {})
    cfg = run.Config(
        dsn="postgresql://example/test",
        user_agent="test",
        fetch_all=True,
        concurrency=2,
        lease_seconds=123,
    )

    result = await run.fetch_all(_Pool(), object(), cfg)

    assert {key for _, key, _ in claimed} == {"feed-1", "feed-2", "feed-3"}
    assert {lease for _, _, lease in claimed} == {123}
    assert result == run.FetchAllResult(eligible=3, polled=1, skipped=1, partial=1)


def _claimed_feed(**overrides) -> Feed:
    base = Feed(
        id=1,
        source="greenhouse",
        key="acme",
        company_id=42,
        source_type="ATS",
        interval_s=900,
        hold_until=NOW + timedelta(hours=1),
    )
    return replace(base, **overrides)


def test_source_instance_prevents_tenant_local_id_collisions():
    openai = Feed(id=1, source="greenhouse", key="openai")
    stripe = Feed(id=2, source="greenhouse", key="stripe")

    assert store.source_instance(openai) == "greenhouse:openai"
    assert store.source_instance(stripe) == "greenhouse:stripe"
    assert (store.source_instance(openai), "123") != (store.source_instance(stripe), "123")


@pytest.mark.asyncio
async def test_company_resolution_checks_exact_and_slug_aliases():
    captured = []

    class Connection:
        async def fetchrow(self, query, candidates):
            captured.extend(candidates)
            return {"company_id": 97}

    assert await store.resolve_company(Connection(), "Ambient AI, Inc.") == 97
    assert captured == ["ambient ai, inc.", "ambient-ai-inc", "ambient-ai"]


def test_raw_wrapper_is_replayable_and_hash_ignores_volatile_fields():
    feed = Feed(id=1, source="workday", key="acme")
    first = Record(
        items=[Item("job-1", {"title": "Engineer", "postedOn": "Posted today"})],
        envelope={"company": "Acme"},
    )
    second = Record(
        items=[Item("job-1", {"title": "Engineer", "postedOn": "Posted yesterday"})],
        envelope={"company": "Acme"},
    )

    prepared_first = router.prepare(feed, first)[0]
    prepared_second = router.prepare(feed, second)[0]

    assert prepared_first.data["payload"]["postedOn"] == "Posted today"
    assert prepared_first.data_hash == prepared_second.data_hash
    replayed, envelope = router.unwrap(prepared_first.data)
    assert replayed.payload == first.items[0].payload
    assert envelope == first.envelope


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("json", {"title": "Engineer"}),
        ("xml", "<job><title>Engineer</title></job>"),
        ("html", "<article>Engineer</article>"),
        ("mime", "Content-Type: text/plain\n\nEngineer"),
    ],
)
def test_raw_wrapper_round_trips_every_supported_transport(kind, payload):
    feed = Feed(id=1, source="fixture", key="acme")
    prepared = router.prepare(
        feed,
        Record(items=[Item("job-1", payload, kind=kind)], envelope={"page": 1}),
    )[0]

    item, envelope = router.unwrap(prepared.data)

    assert item.kind == kind
    assert item.payload == payload
    assert envelope == {"page": 1}


def test_snapshot_hash_is_independent_of_response_order():
    feed = Feed(id=1, source="greenhouse", key="acme")
    one = router.prepare(
        feed,
        Record(items=[Item("a", {"id": 1}), Item("b", {"id": 2})]),
    )
    two = router.prepare(
        feed,
        Record(items=[Item("b", {"id": 2}), Item("a", {"id": 1})]),
    )

    assert router.snapshot_hash(one) == router.snapshot_hash(two)


@pytest.mark.asyncio
async def test_silver_parses_reconstructed_wrapper_inputs(monkeypatch):
    prepared = router.prepare(
        Feed(id=1, source="fixture", key="board"),
        Record(
            items=[Item("job-1", "<article>raw</article>", kind="html")],
            envelope={"tenant": "acme"},
        ),
    )[0]
    stored = router.StoredRaw(
        item=Item("job-1", "stale", kind="mime"),
        raw_id=7,
        data_hash=prepared.data_hash,
        created=True,
        data=prepared.data,
    )
    captured = []

    def parse(feed, item, envelope):
        captured.append((item, envelope))
        return []

    monkeypatch.setattr(silver.sources, "parser", lambda source: parse)

    await silver.process(
        object(),
        Feed(id=1, source="fixture", key="board"),
        [stored],
        {"tenant": "wrong"},
        seen_at=NOW,
    )

    assert captured[0][0] == Item("job-1", "<article>raw</article>", kind="html")
    assert captured[0][1] == {"tenant": "acme"}


def test_schedule_backoff_uses_strikes_and_caps_at_one_day():
    assert work.next_strikes(3, "ok") == 0
    assert work.next_strikes(3, "error") == 4
    assert work.next_strikes(32767, "error") == 32767
    assert work.backoff_s(900, 20) == 86_400
    assert work.next_due_at(900, 0, "error", NOW).timestamp() - NOW.timestamp() == 1800


@pytest.mark.asyncio
async def test_poll_304_touches_postings_and_preserves_etag(monkeypatch):
    feed = _claimed_feed(etag='"old"', res_hash="snapshot")
    calls = []

    async def harvest(feed, net):
        return Record(http_status=304)

    async def touch(conn, source, now):
        calls.append(("touch", source, now))
        return 2

    async def success(conn, claimed, **kwargs):
        calls.append(("success", kwargs))
        return True

    async def observe(conn, **kwargs):
        calls.append(("observe", kwargs))

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "touch_source_postings", touch)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_observation", observe)

    assert await run.poll(_Pool(), object(), feed, NOW) == "not_modified"
    assert calls[0] == ("touch", "greenhouse:acme", NOW)
    assert calls[1][1]["etag"] == '"old"'
    assert calls[1][1]["res_hash"] is None
    assert calls[1][1]["changed"] is False
    assert calls[2] == (
        "observe",
        {"feed_id": 1, "polled_at": NOW, "status": "not_modified", "new_postings": 0},
    )


@pytest.mark.asyncio
async def test_poll_unchanged_hash_skips_raw_and_silver(monkeypatch):
    record = Record(items=[Item("job-1", {"title": "Engineer"})], etag='"new"')
    candidate = _claimed_feed()
    feed = replace(candidate, res_hash=router.snapshot_hash(router.prepare(candidate, record)))
    calls = []

    async def harvest(feed, net):
        return record

    async def touch(conn, source, now):
        calls.append("touch")
        return 1

    async def success(conn, claimed, **kwargs):
        calls.append(("success", kwargs))
        return True

    async def observe(conn, **kwargs):
        calls.append(("observe", kwargs))

    async def unexpected(*args, **kwargs):
        raise AssertionError("unchanged responses must skip bronze and silver")

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "touch_source_postings", touch)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_observation", observe)
    monkeypatch.setattr(router, "persist", unexpected)
    monkeypatch.setattr(silver, "process", unexpected)

    assert await run.poll(_Pool(), object(), feed, NOW) == "ok"
    assert calls[0] == "touch"
    assert calls[1][1]["etag"] == '"new"'
    assert calls[1][1]["changed"] is False
    # A body we already had is still a successful fetch, and it discovered nothing.
    assert calls[2][1]["status"] == "ok"
    assert calls[2][1]["new_postings"] == 0


@pytest.mark.asyncio
async def test_poll_changed_snapshot_runs_bronze_then_silver(monkeypatch):
    feed = _claimed_feed(etag='"old"')
    record = Record(items=[Item("job-1", {"title": "Engineer"})], etag=None)
    events = []

    async def harvest(feed, net):
        return record

    async def persist(conn, feed, prepared):
        events.append("bronze")
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        events.append("silver")
        # Three postings written, but only one of them had never been seen.
        return silver.Counts(postings=3, created=1)

    async def success(conn, claimed, **kwargs):
        events.append(("success", kwargs))
        return True

    async def observe(conn, **kwargs):
        events.append(("observe", kwargs))

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_observation", observe)

    assert await run.poll(_Pool(), object(), feed, NOW) == "ok"
    assert events[:2] == ["bronze", "silver"]
    assert events[2][1]["etag"] is None
    assert events[2][1]["res_hash"] == router.snapshot_hash(router.prepare(feed, record))
    assert events[2][1]["changed"] is True
    # new_postings tracks first sightings, never the two content edits.
    assert events[3][1]["status"] == "ok"
    assert events[3][1]["new_postings"] == 1


@pytest.mark.asyncio
async def test_partial_poll_persists_available_rows_and_backs_off(monkeypatch):
    feed = _claimed_feed()
    record = Record(items=[Item("job-1", {"title": "Engineer"})], truncated=True)
    events = []

    async def harvest(feed, net):
        return record

    async def persist(conn, feed, prepared):
        events.append("bronze")
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        events.append("silver")
        return silver.Counts(postings=1, created=1)

    async def failure(conn, claimed, **kwargs):
        events.append(("failure", kwargs))
        return True

    async def observe(conn, **kwargs):
        events.append(("observe", kwargs))

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "append_poll_observation", observe)

    assert await run.poll(_Pool(), object(), feed, NOW) == "partial"
    assert events[:2] == ["bronze", "silver"]
    assert events[2][0] == "failure"
    assert events[2][1]["due_at"] == NOW + timedelta(seconds=1800)
    # Truncated, but silver still ran: the row it created has to be counted.
    assert events[3][1]["status"] == "partial"
    assert events[3][1]["new_postings"] == 1


@pytest.mark.asyncio
async def test_network_failure_releases_lease_with_backoff(monkeypatch):
    events = []

    async def harvest(feed, net):
        raise OSError("offline")

    async def failure(conn, claimed, **kwargs):
        events.append(kwargs)
        return True

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)

    assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"
    assert events[0]["due_at"] == NOW + timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_lost_lease_records_no_poll_observation(monkeypatch):
    """A reclaimed worker polled this feed too and files its own row.

    Recording ours as well would count one poll twice, which inflates lambda on
    exactly the feeds whose leases expire -- the slow ones.
    """
    feed = _claimed_feed(etag='"old"')
    record = Record(items=[Item("job-1", {"title": "Engineer"})])

    async def harvest(feed, net):
        return record

    async def persist(conn, feed, prepared):
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        return silver.Counts(postings=1, created=1)

    async def lost_lease(conn, claimed, **kwargs):
        return False

    async def unexpected_observation(*args, **kwargs):
        raise AssertionError("a lost lease must not record an observation")

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_success", lost_lease)
    monkeypatch.setattr(store, "append_poll_observation", unexpected_observation)

    assert await run.poll(_Pool(), object(), feed, NOW) == "ok"


@pytest.mark.asyncio
async def test_parser_failure_keeps_bronze_and_does_not_advance_snapshot(monkeypatch):
    feed = _claimed_feed(res_hash="old-snapshot")
    record = Record(items=[Item("job-1", {"title": "Engineer"})])
    events = []

    async def harvest(feed, net):
        return record

    async def persist(conn, feed, prepared):
        events.append("bronze")
        return [router.StoredRaw(prepared[0].item, 7, prepared[0].data_hash, True)]

    async def process(conn, feed, stored, envelope, **kwargs):
        events.append("parser_error")
        raise ValueError("broken parser")

    async def failure(conn, claimed, **kwargs):
        events.append(("failure", kwargs))
        return True

    async def unexpected_success(*args, **kwargs):
        raise AssertionError("parser failures must not advance res_hash")

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(router, "persist", persist)
    monkeypatch.setattr(silver, "process", process)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "finish_schedule_success", unexpected_success)

    assert await run.poll(_Pool(), object(), feed, NOW) == "error"
    assert events[0:2] == ["bronze", "parser_error"]
    assert events[2][0] == "failure"


@pytest.mark.asyncio
async def test_silver_maps_posting_into_job_posting_contract(monkeypatch):
    deadline = datetime(2027, 2, 1, tzinfo=timezone.utc)
    posting = Posting(
        external_id="123",
        title="Platform Intern",
        url_posting="https://example.test/jobs/123",
        apply_url="https://example.test/apply/123",
        company_raw="Acme, Inc.",
        location_raw="Remote, United States",
        description_html="<p>Build <strong>Python</strong> services.</p>",
        workplace_type_raw="Remote",
        deadline_at=deadline,
    )
    feed = Feed(
        id=1,
        source="greenhouse",
        key="acme",
        company_id=42,
        company_name="Acme",
        source_type="ATS",
    )
    stored = [router.StoredRaw(Item("123", {}), raw_id=7, data_hash="raw", created=True)]
    writes = []

    monkeypatch.setattr(silver.sources, "parser", lambda name: lambda feed, item, envelope: [posting])

    async def unresolved(conn, raw):
        return None

    async def write(conn, **kwargs):
        writes.append(kwargs)
        return 9, True, True

    monkeypatch.setattr(store, "resolve_company", unresolved)
    monkeypatch.setattr(store, "upsert_job_posting", write)

    counts = await silver.process(object(), feed, stored, {}, seen_at=NOW)

    assert counts.postings == 1
    assert writes[0]["source"] == "greenhouse:acme"
    assert writes[0]["source_key"] == "123"
    assert writes[0]["company_id"] == 42
    assert writes[0]["raw_id"] == 7
    assert writes[0]["values"] == {
        "title": "Platform Intern",
        "description": "Build Python services.",
        "location_text": "Remote, United States",
        "source_url": "https://example.test/jobs/123",
        "apply_url": "https://example.test/apply/123",
        "posted_at": None,
        "attributes": {
            "version": 1,
            "workplace": "remote",
            "deadline_at": deadline.isoformat(),
        },
        "status": "active",
    }


def test_silver_leaves_derived_enrichment_to_gold():
    posting = Posting(
        external_id="pointer",
        title="Engineer",
        url_posting="https://example.test/jobs/pointer",
        description_html=None,
    )

    values = silver._projection(posting)

    assert values["description"] is None
    assert "opportunity_type" not in values["attributes"]
    assert "opportunity_type_meta" not in values["attributes"]
    assert "skills" not in values["attributes"]
    assert "skills_meta" not in values["attributes"]
    assert "degree_fields" not in values["attributes"]
    assert "degree_fields_meta" not in values["attributes"]


@pytest.mark.asyncio
async def test_changed_silver_posting_invalidates_gold_enrichments():
    queries = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            if query.lstrip().startswith("SELECT"):
                return {"id": 9, "content_hash": "old"}
            return {"id": 9}

    _, changed, created = await store.upsert_job_posting(
        Connection(),
        source="greenhouse:acme",
        source_key="123",
        company_id=42,
        values={
            "title": "Platform Intern",
            "attributes": {"version": 1},
            "status": "active",
        },
        content_hash="new",
        raw_id=7,
        seen_at=NOW,
    )

    assert changed is True
    assert created is False
    upsert = queries[1]
    assert "- 'opportunity_type' - 'opportunity_type_meta'" in upsert
    assert "- 'skills' - 'skills_meta'" in upsert
    assert "- 'degree_fields' - 'degree_fields_meta'" in upsert


@pytest.mark.asyncio
async def test_unresolved_board_posting_stays_bronze(monkeypatch):
    posting = Posting(
        external_id="comment-1",
        title="Engineer",
        url_posting="https://news.example/comment-1",
        company_raw="Unknown Startup",
    )
    feed = Feed(
        id=1,
        source="hn",
        key="monthly",
        company_id=99,
        source_type="Board",
    )
    stored = [router.StoredRaw(Item("comment-1", {}), 7, "raw", True)]
    monkeypatch.setattr(silver.sources, "parser", lambda name: lambda feed, item, envelope: [posting])

    async def unresolved(conn, raw):
        return None

    async def unexpected_write(conn, **kwargs):
        raise AssertionError("unresolved board posting must not reach job_posting")

    monkeypatch.setattr(store, "resolve_company", unresolved)
    monkeypatch.setattr(store, "upsert_job_posting", unexpected_write)

    counts = await silver.process(object(), feed, stored, {}, seen_at=NOW)

    assert counts.unresolved == 1
    assert counts.postings == 0


@pytest.mark.asyncio
async def test_unsupported_outputs_and_secondhand_postings_are_counted(monkeypatch):
    outputs = [
        Target(source="greenhouse", key="discovered"),
        Label(external_id="job-1", name="term", value="summer"),
        Fact(company_raw="Acme", name="headcount", value=10),
        Posting(
            external_id="job-1",
            title="Copied role",
            url_posting="https://aggregator.test/job-1",
            fidelity="secondhand",
        ),
    ]
    feed = Feed(id=1, source="fixture", key="board", source_type="Board")
    stored = [router.StoredRaw(Item("job-1", {}), 7, "raw", True)]
    monkeypatch.setattr(
        silver.sources, "parser", lambda name: lambda feed, item, envelope: outputs
    )

    async def unexpected_write(*args, **kwargs):
        raise AssertionError("unsupported and secondhand outputs must stay out of silver")

    monkeypatch.setattr(store, "upsert_job_posting", unexpected_write)

    counts = await silver.process(object(), feed, stored, {}, seen_at=NOW)

    assert counts.unsupported == 3
    assert counts.secondhand == 1
    assert counts.postings == 0
