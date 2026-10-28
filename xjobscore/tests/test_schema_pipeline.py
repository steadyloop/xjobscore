from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from xjobscore import router, run, silver, sources, store, work
from xjobscore.net import NetError
from xjobscore.records import LINK_DEAD
from xjobscore.records import Fact, Feed, Item, Label, Posting, Record, Target, Unparseable

json_api = sources.json_api


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--enrich"], "all"),
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


def test_seed_cli_flag_is_rejected():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--seed"])


def test_fetch_all_cli_selects_full_sweep_and_concurrency():
    config = run.load_config(
        ["--dsn", "postgresql://example/test", "--fetch-all", "--concurrency", "3"]
    )

    assert config.fetch_all is True
    assert config.concurrency == 3


def test_fetch_all_cli_rejects_zero_concurrency():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--concurrency", "0"])


def test_job_type_gold_enrichment_is_a_cli_choice():
    config = run.load_config(
        ["--dsn", "postgresql://example/test", "--enrich", "job_type"]
    )

    assert config.enrich == "job_type"


def test_batch_cli_flag_is_rejected():
    """Replaced by --cycle-budget-s: a cycle is bounded by time, not a count."""
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--batch", "5"])


@pytest.mark.parametrize(
    "argv, env, expected",
    [
        ([], {}, 30.0),
        (["--cycle-budget-s", "5"], {}, 5.0),
        ([], {"XJOBSCORE_CYCLE_BUDGET_S": "12"}, 12.0),
        (["--cycle-budget-s", "5"], {"XJOBSCORE_CYCLE_BUDGET_S": "12"}, 5.0),
    ],
)
def test_cycle_budget_comes_from_the_flag_then_the_env(argv, env, expected, monkeypatch):
    monkeypatch.delenv("XJOBSCORE_CYCLE_BUDGET_S", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    config = run.load_config(["--dsn", "postgresql://example/test", *argv])
    assert config.cycle_budget_s == expected


def test_cycle_budget_must_be_positive():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--cycle-budget-s", "0"])


def test_per_vendor_claims_have_an_index_and_a_live_migration(live_migration):
    """run.cycle claims per source now, so due_at alone no longer covers it."""
    root = Path(__file__).parent.parent.parent
    index = "CREATE INDEX feed_source_due_idx ON feed (source, due_at) WHERE polling;"
    assert index in (root / "schema.sql").read_text()
    migration = live_migration("20260910_feed_source_due_idx.sql")
    assert (
        "CREATE INDEX IF NOT EXISTS feed_source_due_idx ON feed (source, due_at) WHERE polling"
        in migration
    )


def test_unified_runtime_schema_defaults_live():
    root = Path(__file__).parent.parent.parent
    schema = (root / "schema.sql").read_text()
    assert "running    boolean NOT NULL DEFAULT true" in schema
    assert "CREATE TABLE gold_control" in schema
    assert "CREATE TABLE system_run" in schema


def test_sync_registry_cli_flag_is_rejected():
    with pytest.raises(SystemExit):
        run.load_config(["--dsn", "postgresql://example/test", "--sync-registry"])


@pytest.mark.asyncio
async def test_bare_maintenance_command_directs_to_unified_runtime(
    monkeypatch, capsys
):
    async def unexpected_connect(*args, **kwargs):
        raise AssertionError("a bare maintenance command must not touch PostgreSQL")

    monkeypatch.setattr(store, "connect", unexpected_connect)

    assert await run.main(["--dsn", "postgresql://example/test"]) == 2
    assert "uv run xjobscore" in capsys.readouterr().err


def test_schema_indexes_the_opportunities_filter_and_default_sort():
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()

    assert "job_posting_job_type_active_idx" in schema
    assert "(job_type)" in schema
    assert "job_domain" in schema
    assert "job_posting_domain_active_idx" in schema
    assert "ON job_posting (job_domain, job_type)" in schema
    assert "job_posting_fresh_active_idx" in schema
    assert "((COALESCE(posted_at, first_seen_at)) DESC, id DESC)" in schema


def test_job_domain_has_a_live_database_migration(live_migration):
    migration = live_migration("20260910_job_domain.sql")

    assert "ADD COLUMN IF NOT EXISTS job_domain text" in migration
    assert "ADD COLUMN IF NOT EXISTS job_domain_version smallint" in migration
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS job_posting_domain_active_idx" in migration


def test_schema_carries_workday_count_probe_state():
    """The count probe needs both columns to skip a full poll. Only the schema
    is asserted: `xjobscore/migrations/` is applied against the live database
    and deliberately untracked, so its text is not available to a test."""
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()

    assert "list_total      integer" in schema
    assert "last_full_poll_at timestamptz" in schema


def test_company_alias_rename_has_a_live_database_migration(live_migration):
    """The table is `company_alias`; nothing may still declare the old `alias`
    name, and an existing database has to be carried across by the migration
    rather than only by a fresh schema.sql build."""
    root = Path(__file__).parent.parent.parent
    schema = (root / "schema.sql").read_text()
    assert "CREATE TABLE company_alias (" in schema
    assert "CREATE TABLE alias (" not in schema
    assert "ON company_alias (company_id)" in schema
    migration = live_migration("20260826_company_alias_rename.sql")
    assert "ALTER TABLE alias RENAME TO company_alias" in migration
    assert "alias_company_idx RENAME TO company_alias_company_idx" in migration
    # A table rename leaves the constraints under their old names, which drifts
    # a migrated database from a fresh one; the catalog sweep is what closes it.
    assert "alias_pkey RENAME TO company_alias_pkey" in migration
    assert "RENAME CONSTRAINT" in migration


def test_schema_job_types_match_the_api_contract():
    """job_type has no registry canon any more -- the column CHECK is the
    contract used by the publisher and the web app."""
    root = Path(__file__).parent.parent.parent
    schema = (root / "schema.sql").read_text()
    declared = re.search(
        r"CHECK \(job_type IS NULL OR job_type IN \((.*?)\)\)", schema, re.DOTALL
    )
    assert declared is not None, "schema.sql must CHECK the job_type column"
    schema_types = set(re.findall(r"'([a-z_]+)'", declared.group(1)))
    # `research` has no vendor emitting it yet; it is still part of the contract.
    assert schema_types == {"intern", "coop", "student", "new_grad", "research"}


def test_schema_part_time_is_its_own_column_with_no_negative():
    """Schedule is orthogonal to opportunity kind: a part-time internship is
    `intern` plus part_time 'yes', which the single-valued job_type could not
    say. Domain mirrors `remote` -- no 'no', because a vendor's stated
    full-time value disagrees with the title too often to assert."""
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()
    declared = re.search(r"CHECK \(part_time IN \((.*?)\)\)", schema, re.DOTALL)
    assert declared is not None, "schema.sql must CHECK the part_time column"
    assert set(re.findall(r"'([a-z_]+)'", declared.group(1))) == {"yes", "unknown"}


def test_part_time_split_has_a_live_database_migration(live_migration):
    """The column is new and job_type's domain narrows, so an existing database
    needs the backfill before the tighter CHECK can be installed."""
    migration = live_migration("20260826_part_time_column.sql")
    assert "ADD COLUMN IF NOT EXISTS part_time text" in migration
    # Backfill must precede the narrowed CHECK or the surviving rows violate it.
    assert migration.index("SET part_time = 'yes'") < migration.index(
        "ADD CONSTRAINT job_posting_job_type_check"
    )
    assert "SET job_type = NULL" in migration


def test_schema_remote_domain_has_no_negative():
    """A stated on-site signal is not reliable enough to assert, so the domain
    carries 'unknown' and deliberately has no 'no'."""
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()
    declared = re.search(r"CHECK \(remote IN \((.*?)\)\)", schema, re.DOTALL)
    assert declared is not None
    assert set(re.findall(r"'([a-z]+)'", declared.group(1))) == {
        "yes",
        "hybrid",
        "unknown",
    }


def test_schema_uses_normalized_locations_not_a_scalar_country():
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()

    assert "locations jsonb NOT NULL DEFAULT '[]'::jsonb" in schema
    assert "country char(2)" not in schema
    assert "country_alpha2" not in schema


def test_schema_rejects_retired_and_noncanonical_workday_token_identities():
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()

    assert "CONSTRAINT token_workday_source_renamed" in schema
    assert "CHECK (ats <> 'workday-list')" in schema
    assert "CONSTRAINT token_workday_key_lower" in schema
    assert "CHECK (ats <> 'workday' OR token = lower(token))" in schema


def test_schema_rejects_noncanonical_workday_posting_namespaces():
    schema = (Path(__file__).parent.parent.parent / "schema.sql").read_text()

    assert "CONSTRAINT raw_posting_workday_source_lower" in schema
    assert "CONSTRAINT job_posting_workday_source_lower" in schema
    assert "source NOT LIKE 'workday:%' OR source = lower(source)" in schema


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

    async def fetch(self, query, *args):
        assert "UPDATE job_posting" in query
        return []

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
        type="ATS",
        interval_s=900,
        hold_until=NOW + timedelta(hours=1),
    )
    return replace(base, **overrides)


def test_database_feed_keeps_its_runtime_metadata():
    feed = Feed(
        id=1,
        source="ashby",
        key="bild-ai",
        company_id=42,
        company_name="bild-ai",
        config={"company_raw": "Bild"},
        type="ATS",
    )

    assert feed.company_id == 42
    assert feed.company_name == "bild-ai"
    assert feed.config == {"company_raw": "Bild"}
    assert feed.type == "ATS"


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


@pytest.mark.asyncio
async def test_company_resolution_from_alias_map_skips_the_database():
    class Connection:
        async def fetchrow(self, query, candidates):
            raise AssertionError("aliases map should make this call unreachable")

    aliases = {"ambient-ai-inc": 97}

    # Falls back through candidates in the same exact -> slug -> stripped-suffix
    # order the query form uses; only the slug entry is present here.
    assert await store.resolve_company(Connection(), "Ambient AI, Inc.", aliases=aliases) == 97
    # A miss is authoritative -- the map is the whole table, so no DB fallback.
    assert await store.resolve_company(Connection(), "Nobody Here LLC", aliases=aliases) is None


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

    async def touch(conn, source, now, *, feed_id=None):
        calls.append(("touch", source, now, feed_id))
        return 2

    async def success(conn, claimed, **kwargs):
        calls.append(("success", kwargs))
        return True

    async def observe(conn, **kwargs):
        calls.append(("observe", kwargs))

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "touch_source_postings", touch)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_log", observe)

    assert await run.poll(_Pool(), object(), feed, NOW) == "unchanged"
    # feed_id is what scopes the touch to postings still in the snapshot; a
    # blanket touch would re-stamp postings that vanished on an earlier poll.
    assert calls[0] == ("touch", "greenhouse:acme", NOW, feed.id)
    assert calls[1][1]["etag"] == '"old"'
    assert calls[1][1]["res_hash"] is None
    assert calls[1][1]["changed"] is False
    assert calls[2] == (
        "observe",
        {"feed_id": 1, "polled_at": NOW, "status": "unchanged", "new_postings": 0},
    )


@pytest.mark.asyncio
async def test_poll_count_probe_uses_the_unchanged_path(monkeypatch):
    feed = _claimed_feed(res_hash="snapshot")
    calls = []

    async def harvest(feed, net):
        return Record(http_status=200, count_unchanged=True, list_total=17)

    async def touch(conn, source, now, *, feed_id=None):
        calls.append(("touch", source, feed_id))
        return 17

    async def success(conn, claimed, **kwargs):
        calls.append(("success", kwargs))
        return True

    async def observe(conn, **kwargs):
        calls.append(("observe", kwargs))

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "touch_source_postings", touch)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_log", observe)

    assert await run.poll(_Pool(), object(), feed, NOW) == "unchanged"
    assert calls[0] == ("touch", "greenhouse:acme", feed.id)
    assert calls[1][1]["changed"] is False
    assert calls[2][1]["status"] == "unchanged"


@pytest.mark.asyncio
async def test_poll_unchanged_hash_skips_raw_and_silver(monkeypatch):
    record = Record(
        items=[Item("job-1", {"title": "Engineer"})],
        etag='"new"',
        list_total=1,
        full_listing=True,
    )
    candidate = _claimed_feed()
    feed = replace(candidate, res_hash=router.snapshot_hash(router.prepare(candidate, record)))
    calls = []

    async def harvest(feed, net):
        return record

    async def touch(conn, source, now, *, feed_id=None):
        calls.append(("touch", feed_id))
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
    monkeypatch.setattr(store, "append_poll_log", observe)
    monkeypatch.setattr(router, "persist", unexpected)
    monkeypatch.setattr(silver, "process", unexpected)

    assert await run.poll(_Pool(), object(), feed, NOW) == "ok"
    success = next(payload for name, payload in calls if name == "success")
    assert success["list_total"] == 1
    assert success["full_listing"] is True
    assert calls[0] == ("touch", feed.id)
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
    monkeypatch.setattr(store, "append_poll_log", observe)

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
    monkeypatch.setattr(store, "append_poll_log", observe)

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
@pytest.mark.parametrize(
    ("raised", "traceback_logged"),
    [
        # A deleted board is a vendor verdict, not a fault to debug.
        (NetError("HTTP 404", url="https://x/jobs", status=404), False),
        (NetError("HTTP 500", url="https://x/jobs", status=500), True),
        (ValueError("unknown url type"), True),
    ],
)
async def test_only_an_unexpected_harvest_failure_logs_a_traceback(
    monkeypatch, caplog, raised, traceback_logged
):
    """404s are the bulk of failures on a full queue sweep; a stack trace each
    buries the harvest bugs worth reading."""
    async def harvest(feed, net):
        raise raised

    async def failure(conn, feed, *, now, due_at, etag=None):
        return True

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)

    with caplog.at_level(logging.WARNING, logger="xjobscore.run"):
        assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"

    # The harvest record only: the 404 path also runs the gone-check, which
    # logs its own failure against this test's fake pool.
    harvest_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith(("board gone", "harvest failed"))
    ]
    assert len(harvest_records) == 1
    assert (harvest_records[0].exc_info is not None) is traceback_logged


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (NetError("HTTP 404", url="https://x/jobs", status=404), 404),
        # A vendor answering the job endpoint with an HTML page: the request
        # succeeded, so the 200 is the whole point of recording it.
        (NetError("body is not JSON", url="https://x/jobs", status=200), 200),
        # Answered by nobody -- there is no status to record, and a made-up one
        # would read as a vendor verdict.
        (NetError("timed out", url="https://x/jobs"), None),
        # A config that leaves the URL template unresolved never reaches HTTP.
        (ValueError("unknown url type"), None),
    ],
)
async def test_failed_poll_records_the_status_behind_it(monkeypatch, raised, expected):
    """`status` says a poll failed; `status_code` says what the vendor answered.

    Without it a dead board, a blocked request and a broken config are one
    indistinguishable 'error' -- the state that hid nine empty-config feeds.
    """
    rows = []

    async def harvest(feed, net):
        raise raised

    async def failure(conn, claimed, **kwargs):
        return True

    async def observe(conn, **kwargs):
        rows.append(kwargs)

    async def not_gone(conn, feed_id):
        return 0

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "append_poll_log", observe)
    # This test is about what gets recorded, not about dropping: the 404 case
    # would otherwise reach the gone-check and the fake connection behind it.
    monkeypatch.setattr(store, "gone_strikes", not_gone)

    assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"
    assert rows[0]["status"] == "error"
    assert rows[0]["status_code"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strikes", "dropped"),
    [
        (1, False),
        (2, False),
        # The third 404 IS the drop -- not a flag another pass acts on later.
        (3, True),
        (4, True),
    ],
)
async def test_repeated_404_drops_the_feed_on_the_third_strike(monkeypatch, strikes, dropped):
    """A board that answers 404 is gone, not busy; retrying it daily is waste.

    Dropped rather than muted: `feed` goes and `poll_log` cascades with it,
    while `token`, `company`, bronze and postings survive, so a later import
    can bring the feed back.
    """
    drops = []

    async def harvest(feed, net):
        raise NetError("HTTP 404", url="https://x/jobs", status=404)

    async def failure(conn, claimed, **kwargs):
        return True

    async def observe(conn, **kwargs):
        return None

    async def count(conn, feed_id):
        return strikes

    async def drop(conn, source, key):
        drops.append((source, key))
        return 7

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "append_poll_log", observe)
    monkeypatch.setattr(store, "gone_strikes", count)
    monkeypatch.setattr(store, "drop_feed", drop)

    assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"
    assert drops == ([("greenhouse", "acme")] if dropped else [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised",
    [
        # Rate limiting and outages are exactly what backoff is for.
        NetError("HTTP 429", url="https://x/jobs", status=429),
        NetError("HTTP 503", url="https://x/jobs", status=503),
        # The shared transport blips that hit every vendor at once. These are
        # the reason the count reads `status_code` instead of `feed.strikes`.
        NetError("timed out", url="https://x/jobs"),
    ],
)
async def test_failures_that_are_not_404_never_drop_a_feed(monkeypatch, raised):
    drops = []

    async def harvest(feed, net):
        raise raised

    async def failure(conn, claimed, **kwargs):
        return True

    async def observe(conn, **kwargs):
        return None

    async def unexpected_count(conn, feed_id):
        raise AssertionError("gone_strikes consulted for a non-404 failure")

    async def drop(conn, source, key):
        drops.append((source, key))
        return 7

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", failure)
    monkeypatch.setattr(store, "append_poll_log", observe)
    monkeypatch.setattr(store, "gone_strikes", unexpected_count)
    monkeypatch.setattr(store, "drop_feed", drop)

    assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"
    assert drops == []


@pytest.mark.asyncio
async def test_a_lost_lease_drops_nothing(monkeypatch):
    """Another worker owns this feed now; it files the row that would count."""
    drops = []

    async def harvest(feed, net):
        raise NetError("HTTP 404", url="https://x/jobs", status=404)

    async def lost_lease(conn, claimed, **kwargs):
        return False

    async def unexpected_count(conn, feed_id):
        raise AssertionError("gone_strikes consulted without the lease")

    async def drop(conn, source, key):
        drops.append((source, key))
        return 7

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "finish_schedule_failure", lost_lease)
    monkeypatch.setattr(store, "gone_strikes", unexpected_count)
    monkeypatch.setattr(store, "drop_feed", drop)

    assert await run.poll(_Pool(), object(), _claimed_feed(), NOW) == "error"
    assert drops == []


@pytest.mark.asyncio
async def test_lost_lease_records_no_poll_log(monkeypatch):
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
    monkeypatch.setattr(store, "append_poll_log", unexpected_observation)

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
        type="ATS",
    )
    stored = [router.StoredRaw(Item("123", {}), raw_id=7, data_hash="raw", created=True)]
    writes = []

    monkeypatch.setattr(silver.sources, "parser", lambda name: lambda feed, item, envelope: [posting])

    async def unresolved(conn, raw, **kwargs):
        return None

    async def write(conn, **kwargs):
        writes.append(kwargs)
        return 9, True, True

    monkeypatch.setattr(store, "resolve_company", unresolved)
    monkeypatch.setattr(store, "upsert_job_posting", write)

    counts = await silver.process(object(), feed, stored, {}, seen_at=NOW)

    assert counts.postings == 1
    assert counts.changed_posting_ids == frozenset({9})
    assert counts.affected_company_ids == frozenset({42})
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
        # Parse-time, vendor-specific: this fixture's adapter derives neither,
        # so silver carries the "no signal" defaults through untouched.
        "job_type": None,
        "remote": "unknown",
        "part_time": "unknown",
        "locations": [{"country": "US", "state": None, "city": None}],
        "attributes": {
            "version": 1,
            "remote": "yes",
            "deadline_at": deadline.isoformat(),
        },
        "status": "active",
    }


def test_silver_projects_ashby_student_from_parse_rules():
    feed = Feed(id=1, source="ashby", key="acme")
    posting = json_api.parse(
        "ashby",
        feed,
        Item("student-1", {
            "title": "Working Student, Software Engineering",
            "employmentType": "PartTime",
            "department": "Engineering",
            "team": "Engineering",
            "descriptionPlain": "",
        }),
        {},
    )[0]

    values = silver._projection(posting)

    assert posting.job_type == "student"
    assert values["job_type"] == "student"


def test_ashby_stores_and_normalizes_the_postal_augmented_location_text():
    feed = Feed(id=1, source="ashby", key="acme")
    posting = json_api.parse(
        "ashby",
        feed,
        Item("location-1", {
            "title": "Engineer",
            "location": "Santa Clara",
            "address": {
                "postalAddress": {
                    "addressLocality": "Santa Clara",
                    "addressCountry": "United States",
                },
            },
        }),
        {},
    )[0]

    values = silver._projection(posting)

    assert posting.location_raw == "Santa Clara"
    assert posting.location_normalization_raw == (
        "Santa Clara, Santa Clara, United States"
    )
    assert values["location_text"] == "Santa Clara, Santa Clara, United States"
    assert values["locations"] == [
        {"country": "US", "state": "CA", "city": "Santa Clara"}
    ]


def test_greenhouse_normalizes_offices_separately_without_changing_primary_text():
    feed = Feed(id=1, source="greenhouse", key="acme")
    posting = json_api.parse(
        "greenhouse",
        feed,
        Item("location-1", {
            "title": "Engineer",
            "location": {"name": "London, United Kingdom"},
            "offices": [
                {"name": "United States of America"},
                {"name": "San Mateo"},
            ],
        }),
        {},
    )[0]

    values = silver._projection(posting)

    assert posting.location_raw == "London, United Kingdom"
    assert posting.location_normalization_extra == [
        "United States of America",
        "San Mateo",
    ]
    assert values["location_text"] == "London, United Kingdom"
    assert values["locations"] == [
        {"country": "GB", "state": "ENG", "city": "London"},
        {"country": "US", "state": None, "city": None},
        {"country": "US", "state": "CA", "city": "San Mateo"},
    ]


@pytest.mark.parametrize(("location", "country_code", "expected"), [
    ("Amsterdam, Noord-Holland, Nederland", "NL",
     [{"country": "NL", "state": "07", "city": "Amsterdam"}]),
    ("Rijssen, Overijssel, Nederland", "NL",
     [{"country": "NL", "state": "15", "city": "Rijssen"}]),
    ("Koudekerk aan den Rijn, Zuid-Holland, Nederland", "NL",
     [{"country": "NL", "state": "11", "city": None}]),
    ("Nederland, TX, United States", "US",
     [{"country": "US", "state": "TX", "city": "Nederland"}]),
    ("Amsterdam, Noord-Holland, Nederland; Austin, TX", "NL",
     [{"country": "NL", "state": "07", "city": "Amsterdam"},
      {"country": "US", "state": "TX", "city": "Austin"}]),
])
def test_recruitee_location_projection_preserves_real_us_eligibility(location, country_code, expected):
    posting = json_api.parse(
        "recruitee", Feed(id=1, source="recruitee", key="acme"),
        Item("location-regression", {
            "title": "Software Intern", "location": location,
            "country_code": country_code,
            "careers_url": "https://acme.recruitee.com/o/software-intern",
        }), {},
    )[0]
    values = silver._projection(posting)
    assert values['location_text'] == location
    assert values['locations'] == expected
    assert values['status'] == 'active'


@pytest.mark.parametrize(("us_address", "expected"), [
    ({"name": "Voortman US", "city": "Monee", "state": "Illinois", "country_code": "US"},
     {"country": "US", "state": "IL", "city": "Monee"}),
    ({"name": "Voortman US", "city": "Fresno", "state": "California", "country_code": "US"},
     {"country": "US", "state": "CA", "city": "Fresno"}),
    ({"name": "Unlisted locality", "city": "Unknown town", "state": "Texas", "country_code": "US"},
     {"country": "US", "state": "TX", "city": None}),
    ({"country_code": "US"}, {"country": "US", "state": None, "city": None}),
])
def test_recruitee_dutch_primary_preserves_explicit_us_secondary_address(us_address, expected):
    posting = json_api.parse(
        "recruitee", Feed(id=1, source="recruitee", key="voortman"),
        Item("mixed-offices", {
            "title": "Software Intern", "location": "Rijssen, Overijssel, Nederland",
            "country_code": "NL", "locations": [
                {"name": "Voortman NL", "city": "Rijssen", "state": "Overijssel", "country_code": "NL"},
                us_address, us_address,
            ],
        }), {},
    )[0]
    values = silver._projection(posting)
    assert values['location_text'] == 'Rijssen, Overijssel, Nederland'
    assert values['locations'][0] == {'country':'NL', 'state':'15', 'city':'Rijssen'}
    us = [place for place in values['locations'] if place['country']=='US']
    assert us == [expected]


@pytest.mark.parametrize("addresses", [None, [], [{"country_code":"NL"}], ["invalid", None, {}]])
def test_recruitee_missing_or_foreign_secondary_addresses_do_not_invent_us_eligibility(addresses):
    posting = json_api.parse(
        "recruitee", Feed(id=1, source="recruitee", key="acme"),
        Item("foreign-offices", {
            "title": "Software Intern", "location": "Amsterdam, Noord-Holland, Nederland",
            "locations": addresses,
        }), {},
    )[0]
    assert silver._projection(posting)['locations'] == [
        {'country':'NL','state':'07','city':'Amsterdam'}
    ]


@pytest.mark.parametrize(
    ("payload", "location_text"),
    [
        ({"location": "Berlin"}, "Berlin"),
        (
            {
                "location": "Toronto",
                "address": {"postalAddress": {"addressCountry": "Canada"}},
            },
            "Toronto, Canada",
        ),
        (
            {
                "address": {
                    "postalAddress": {
                        "addressLocality": "Toronto",
                        "addressCountry": "Canada",
                    }
                }
            },
            "Toronto, Canada",
        ),
    ],
)
def test_ashby_location_context_skips_missing_parts(payload, location_text):
    posting = json_api.parse(
        "ashby",
        Feed(id=1, source="ashby", key="acme"),
        Item("location-1", {"title": "Engineer", **payload}),
        {},
    )[0]

    values = silver._projection(posting)

    assert values["location_text"] == location_text
    assert values["locations"]


def test_ashby_secondary_locations_are_additive_and_never_remove_primary_cities():
    posting = json_api.parse(
        "ashby",
        Feed(id=1, source="ashby", key="acme"),
        Item("location-1", {
            "title": "Engineer",
            "location": "San Francisco",
            "address": {
                "postalAddress": {
                    "addressLocality": "San Francisco",
                    "addressCountry": "United States",
                }
            },
            "secondaryLocations": [
                {
                    "location": "New York",
                    "address": {
                        "postalAddress": {
                            "addressLocality": "New York",
                            "addressCountry": "United States",
                        }
                    },
                },
                {
                    "location": "San Francisco",
                    "address": {
                        "postalAddress": {
                            "addressLocality": "San Francisco",
                            "addressCountry": "United States",
                        }
                    },
                },
            ],
        }),
        {},
    )[0]

    primary_only = silver._projection(
        replace(posting, location_normalization_extra=[])
    )["locations"]
    values = silver._projection(posting)

    assert values["location_text"] == (
        "San Francisco, San Francisco, United States"
    )
    assert values["locations"] == [
        {"country": "US", "state": "CA", "city": "San Francisco"},
        {"country": "US", "state": "NY", "city": "New York City"},
    ]
    assert {
        (location["country"], location["state"], location["city"])
        for location in primary_only
    } <= {
        (location["country"], location["state"], location["city"])
        for location in values["locations"]
    }


def test_silver_projects_greenhouse_student_from_parse_rules():
    feed = Feed(id=1, source="greenhouse", key="acme")
    posting = json_api.parse(
        "greenhouse",
        feed,
        Item("student-1", {
            "title": "Working Student, Software Engineering",
            "company_name": "Acme",
            "location": {"name": "Berlin"},
            "metadata": [],
            "departments": [],
        }),
        {},
    )[0]

    values = silver._projection(posting)

    assert posting.job_type == "student"
    assert values["job_type"] == "student"


_BASE_HASH_VALUES = {
    "title": "Platform Intern",
    "description": "Build Python services.",
    "location_text": "Remote, United States",
    "source_url": "https://example.test/jobs/123",
    "apply_url": "https://example.test/apply/123",
    "posted_at": None,
    "job_type": None,
    "remote": "unknown",
    "locations": [],
    "attributes": {"version": 1, "remote": "yes"},
    "status": "active",
}


def test_content_hash_ignores_status():
    """status is write-once in the upsert; hashing it would report a change
    the stored row can never reflect."""
    active = silver._content_hash(_BASE_HASH_VALUES)
    closed = silver._content_hash({**_BASE_HASH_VALUES, "status": "closed"})

    assert active == closed


def test_content_hash_ignores_attributes():
    """No gold step reads deadline_at or attributes' silver-derived remote --
    skills and degree hash only their posting-text inputs."""
    without_deadline = silver._content_hash(_BASE_HASH_VALUES)
    with_deadline = silver._content_hash(
        {
            **_BASE_HASH_VALUES,
            "attributes": {
                "version": 1,
                "remote": "yes",
                "deadline_at": "2027-02-01T00:00:00+00:00",
            },
        }
    )

    assert without_deadline == with_deadline


@pytest.mark.parametrize(
    "field",
    ["title", "description", "location_text", "source_url", "apply_url", "posted_at"],
)
def test_content_hash_covers_tracked_fields(field):
    base = silver._content_hash(_BASE_HASH_VALUES)
    changed = silver._content_hash({**_BASE_HASH_VALUES, field: "changed-value"})

    assert base != changed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_type", "intern"),
        ("remote", "yes"),
        ("locations", [{"country": "US", "state": None, "city": None}]),
    ],
)
def test_content_hash_ignores_parsed_fields(field, value):
    base = silver._content_hash(_BASE_HASH_VALUES)
    changed = silver._content_hash({**_BASE_HASH_VALUES, field: value})

    assert base == changed


def test_silver_leaves_derived_enrichment_to_gold():
    posting = Posting(
        external_id="pointer",
        title="Engineer",
        url_posting="https://example.test/jobs/pointer",
        description_html=None,
    )

    values = silver._projection(posting)

    assert values["description"] is None
    assert "job_type" not in values["attributes"]
    assert "job_type_meta" not in values["attributes"]
    assert "skills" not in values["attributes"]
    assert "skills_meta" not in values["attributes"]
    assert "degree" not in values["attributes"]


@pytest.mark.parametrize(
    ("location_raw", "remote"),
    [
        ("New York, NY (Remote)", "yes"),
        ("Hybrid - San Francisco", "hybrid"),
        # An explicit on-site statement is still not "no" -- it stays absent,
        # same as a location string with no signal at all.
        ("On-site - Austin, TX", None),
        ("New York, NY", None),
    ],
)
def test_silver_falls_back_to_location_text_for_remote(location_raw, remote):
    posting = Posting(
        external_id="loc",
        title="Engineer",
        url_posting="https://example.test/jobs/loc",
        location_raw=location_raw,
    )

    values = silver._projection(posting)

    assert values["attributes"].get("remote") == remote


def test_silver_prefers_structured_remote_over_location_text():
    posting = Posting(
        external_id="both",
        title="Engineer",
        url_posting="https://example.test/jobs/both",
        workplace_type_raw="Hybrid",
        location_raw="Fully Remote, US",
    )

    values = silver._projection(posting)

    assert values["attributes"]["remote"] == "hybrid"


@pytest.mark.asyncio
async def test_description_change_invalidates_gold_enrichments():
    queries = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            # A single WITH-based upsert: `prev` sees the pre-upsert row, so
            # the fake reports the same "existing row, different hash" case
            # the two-query form used to, just from one call.
            return {"id": 9, "changed": True, "created": False}

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
    upsert = queries[0]
    # job_type is no longer a gold artifact, so it is not invalidated here --
    # it is overwritten unconditionally from the adapter on every upsert.
    assert "job_type = EXCLUDED.job_type" in upsert
    assert "remote = EXCLUDED.remote" in upsert
    assert "locations = EXCLUDED.locations" in upsert
    assert "country = EXCLUDED.country" not in upsert
    assert "job_type_meta" not in upsert
    assert "job_domain = CASE" in upsert
    assert "job_domain_version = CASE" in upsert
    assert "job_posting.title IS DISTINCT FROM EXCLUDED.title" in upsert
    assert (
        "job_posting.description IS DISTINCT FROM EXCLUDED.description" in upsert
    )
    assert (
        "job_posting.content_hash IS DISTINCT FROM EXCLUDED.content_hash" not in upsert
    )
    assert "- 'skills' - 'skills_meta'" in upsert
    assert "- 'degree' - 'degree_meta'" in upsert
    assert upsert.count("- 'summary' - 'summary_meta'") == 2
    # Focus chips read title, description and employer, and exist only on
    # typed postings, so any of those changing drops them.
    assert upsert.count("- 'focus' - 'focus_meta'") == 3
    # The tag and its revision stamp must be invalidated together.
    assert "degree_version = CASE" in upsert


@pytest.mark.asyncio
async def test_status_is_monotonic_not_write_once():
    """A stated closure must land; nothing may reopen the row.

    Both halves matter and they pull in opposite directions. Without the first,
    the pipeline can never close anything it did not close on insert. With a
    symmetric `status = EXCLUDED.status`, the next list harvest -- which
    projects 'active' for every row it returns -- would reopen every closed
    posting. Only the CASE satisfies both.
    """
    queries = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            return {"id": 9, "changed": True, "created": False}

    await store.upsert_job_posting(
        Connection(),
        source="greenhouse:acme",
        source_key="123",
        company_id=42,
        values={"title": "Intern", "attributes": {"version": 1}, "status": "closed"},
        content_hash="new",
        raw_id=7,
        seen_at=NOW,
    )

    upsert = queries[0]
    assert "WHEN EXCLUDED.status = 'closed' THEN 'closed'" in upsert
    assert "ELSE job_posting.status" in upsert
    assert "status = EXCLUDED.status" not in upsert


@pytest.mark.asyncio
async def test_replay_never_advances_last_seen_at():
    """`last_seen_at` means "was in a listing", and a replay reads no listing.

    `latest_raw` returns every source key the feed has *ever* produced, so a
    replay that stamped `last_seen_at` would mark every vanished posting as
    freshly seen and leave the absence signal permanently empty.
    """
    queries = []
    captured = []

    class Connection:
        async def fetchrow(self, query, *args):
            queries.append(query)
            captured.append(args)
            return {"id": 9, "changed": True, "created": False}

    await store.upsert_job_posting(
        Connection(),
        source="greenhouse:acme",
        source_key="123",
        company_id=42,
        values={"title": "Intern", "attributes": {"version": 1}, "status": "active"},
        content_hash="new",
        raw_id=7,
        seen_at=NOW,
        touch_seen=False,
    )

    assert "WHEN $18 THEN EXCLUDED.last_seen_at" in queries[0]
    assert "ELSE job_posting.last_seen_at" in queries[0]
    assert captured[0][17] is False


@pytest.mark.asyncio
async def test_probed_closure_never_touches_canonical_columns(monkeypatch):
    """A tombstone annotates a row; it must not rewrite one.

    A probe carries no title, body, or URL. The canonical upsert refreshes all
    three from whatever projection it is handed, so routing a tombstone through
    it would blank the real posting -- which is exactly why silver branches to
    a narrow closure write instead.
    """
    tombstone = Posting(
        external_id="123",
        title="",
        url_posting="",
        state="closed",
        closed_evidence=LINK_DEAD,
    )
    feed = _claimed_feed()
    stored = [router.StoredRaw(Item("123", {"_probe": {"outcome": "absent"}}), 7, "raw", True)]
    monkeypatch.setattr(
        silver.sources, "parser", lambda name: lambda feed, item, envelope: [tombstone]
    )

    closed = []

    async def close_posting_by_key(conn, *, source, source_key, closed_at):
        closed.append((source, source_key, closed_at))
        return True

    async def unexpected(*args, **kwargs):
        raise AssertionError("a probed closure must not run the canonical upsert")

    monkeypatch.setattr(store, "close_posting_by_key", close_posting_by_key)
    monkeypatch.setattr(store, "upsert_job_posting", unexpected)

    counts = await silver.process(
        _Connection(), feed, stored, {}, seen_at=NOW, touch_seen=False
    )

    assert closed == [("greenhouse:acme", "123", NOW)]
    assert counts.postings == 1


def test_a_live_or_blocked_probe_parses_to_nothing():
    """Only `absent` becomes a closure. The other two stay bronze-only.

    Both are worth storing -- they are what a later analysis measures the probe
    channel's own reliability against -- but neither changes what silver
    believes, and an `inconclusive` that closed a posting would be the exact
    fabricated closure the taxonomy exists to prevent.
    """
    feed = Feed(id=1, source="greenhouse", key="acme")

    for outcome in ("live", "inconclusive"):
        item = Item("123", {"_probe": {"outcome": outcome, "url": "https://x.test"}})
        assert json_api.parse("greenhouse", feed, item, {}) == []

    gone = Item("123", {"_probe": {"outcome": "absent", "url": "https://x.test"}})
    postings = json_api.parse("greenhouse", feed, gone, {})
    assert len(postings) == 1
    assert postings[0].state == "closed"
    assert postings[0].closed_evidence == LINK_DEAD


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
        type="Board",
    )
    stored = [router.StoredRaw(Item("comment-1", {}), 7, "raw", True)]
    monkeypatch.setattr(silver.sources, "parser", lambda name: lambda feed, item, envelope: [posting])

    async def unresolved(conn, raw, **kwargs):
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
    feed = Feed(id=1, source="fixture", key="board", type="Board")
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


@pytest.mark.asyncio
async def test_one_unparseable_item_does_not_cost_the_batch(monkeypatch):
    """A detail page the vendor no longer serves is one skipped item.

    Taleo answers 200 with its career-section shell for a requisition pulled
    between the listing walk and the detail fetch. Failing the whole poll on
    that threw away every other posting fetched beside it.
    """
    posting = Posting(
        external_id="job-2",
        title="Kept",
        url_posting="https://example.test/jobs/2",
        company_raw="Acme",
    )
    feed = Feed(id=1, source="fixture", key="board", company_id=42, type="ATS")
    stored = [
        router.StoredRaw(Item("job-1", {}), 7, "raw-1", True),
        router.StoredRaw(Item("job-2", {}), 8, "raw-2", True),
    ]

    def parse(feed, item, envelope):
        if item.external_id == "job-1":
            raise Unparseable("Taleo page did not define descRequisition")
        return [posting]

    monkeypatch.setattr(silver.sources, "parser", lambda name: parse)

    writes = []

    async def unresolved(conn, raw, **kwargs):
        return None

    async def write(conn, **kwargs):
        writes.append(kwargs["source_key"])
        return 9, True, True

    monkeypatch.setattr(store, "resolve_company", unresolved)
    monkeypatch.setattr(store, "upsert_job_posting", write)

    counts = await silver.process(object(), feed, stored, {}, seen_at=NOW)

    assert counts.unparseable == 1
    assert counts.postings == 1
    assert writes == ["job-2"]


@pytest.mark.asyncio
async def test_a_wholly_unparseable_batch_is_still_an_error(monkeypatch):
    """Skipping every item would report a broken feed as a healthy empty one."""
    feed = Feed(id=1, source="fixture", key="board", company_id=42, type="ATS")
    stored = [
        router.StoredRaw(Item("job-1", {}), 7, "raw-1", True),
        router.StoredRaw(Item("job-2", {}), 8, "raw-2", True),
    ]

    def parse(feed, item, envelope):
        raise Unparseable("Taleo page did not define descRequisition")

    monkeypatch.setattr(silver.sources, "parser", lambda name: parse)

    with pytest.raises(ValueError, match="every item was unparseable"):
        await silver.process(object(), feed, stored, {}, seen_at=NOW)


class _RecordingConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_a_listing_that_reused_detail_records_the_total_but_not_the_reconciliation():
    """`last_full_poll_at` is the clock `sources.reusing` bounds detail reuse
    by. A count-probe source enumerates its board on most polls, so advancing
    it on every listing kept reuse fresh forever and a description-only edit
    was never refetched. The total still moves -- it is the probe's baseline."""
    feed = _claimed_feed()
    conn = _RecordingConnection()

    await store.finish_schedule_success(
        conn, feed, now=NOW, due_at=NOW, etag=None, res_hash="h", changed=False,
        list_total=12, full_listing=True, reused_detail=True,
    )

    query, args = conn.calls[0]
    assert "list_total = COALESCE($8::integer, list_total)" in query
    assert "last_full_poll_at = CASE WHEN $9 AND NOT $12 THEN $2" in query
    assert args[8] is True and args[11] is True  # $9 full_listing, $12 reused_detail


@pytest.mark.asyncio
async def test_poll_reports_reused_detail_to_the_schedule(monkeypatch):
    feed = _claimed_feed(res_hash="snapshot")
    seen = []

    async def harvest(feed, net):
        return Record(
            items=[Item("job-1", {"title": "Intern"})], http_status=200,
            list_total=1, full_listing=True, reused_item_count=1,
        )

    async def success(conn, claimed, **kwargs):
        seen.append(kwargs)
        return True

    async def nothing(*args, **kwargs):
        return 0

    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(run.router, "snapshot_hash", lambda prepared: "snapshot")
    monkeypatch.setattr(store, "touch_source_postings", nothing)
    monkeypatch.setattr(store, "finish_schedule_success", success)
    monkeypatch.setattr(store, "append_poll_log", nothing)

    assert await run.poll(_Pool(), object(), feed, NOW) == "ok"
    assert seen[0]["full_listing"] is True
    assert seen[0]["reused_detail"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_poll_finalizes_with_the_renewed_lease_token(monkeypatch, failed):
    from dataclasses import replace
    feed = _claimed_feed()
    renewed = replace(feed, hold_until=feed.hold_until + timedelta(hours=1))
    seen = []

    class Lease:
        def __init__(self, pool, initial, now):
            self.feed = initial

        async def run(self, operation):
            try:
                return await operation()
            finally:
                self.feed = renewed

    async def harvest(feed, net):
        if failed:
            raise RuntimeError("fetch failed after renewal")
        return Record(http_status=304)

    async def finish(conn, claimed, **kwargs):
        seen.append(claimed.hold_until)
        return True

    async def nothing(*args, **kwargs):
        return 0

    monkeypatch.setattr(run, "FeedLease", Lease)
    monkeypatch.setattr(run.sources, "get", lambda source: harvest)
    monkeypatch.setattr(store, "touch_source_postings", nothing)
    monkeypatch.setattr(store, "finish_schedule_success", finish)
    monkeypatch.setattr(store, "finish_schedule_failure", finish)
    monkeypatch.setattr(store, "append_poll_log", nothing)
    monkeypatch.setattr(run.company_domain, "try_for_feed", nothing)
    assert await run.poll(_Pool(), object(), feed, NOW) == ("error" if failed else "unchanged")
    assert seen == [renewed.hold_until]
