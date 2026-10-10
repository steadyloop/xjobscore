"""ResultStore behavior: latest payload staging and latency history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from xjobscore.feed_identity import feed_ref
from xjobscore.console.cache import RunSnapshot
from xjobscore.console.persistence import _rename_legacy_schema

NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
REF = feed_ref("lever", "acme")
# Staging is keyed (source, feed_key); only fetch_latency still uses the ref.
FEED = ("lever", "acme")


def snapshot(result_id: str, *, finished_at: datetime = NOW, **overrides) -> RunSnapshot:
    values = dict(
        result_id=result_id,
        feed_id=3,
        source="lever",
        key="acme",
        started_at=finished_at,
        finished_at=finished_at,
        elapsed_ms=12.5,
        status="ok",
        http_status=200,
        item_count=1,
        raw_size_bytes=18,
        truncated=False,
        error_class=None,
        error_detail=None,
        items=(
            {
                "external_id": "job-1",
                "kind": "json",
                "payload": {"title": "Demo"},
                "raw_document": {"content_hash": "abc"},
            },
        ),
        envelope={"company": "Acme"},
        size_bytes=100,
        network={"summary": {"request_count": 1, "retry_count": 2}, "requests": []},
    )
    values.update(overrides)
    return RunSnapshot(**values)


async def test_history_retention_prunes_payload_but_keeps_series(store_factory):
    store = await store_factory(history_limit=2)
    for offset in range(3):
        await store.save(
            snapshot(f"run-{offset}", finished_at=NOW + timedelta(minutes=offset))
        )

    history = await store.run_history(REF)
    loaded = await store.load_ref(*FEED)

    assert [entry["result_id"] for entry in history] == ["run-2", "run-1"]
    assert loaded is not None and loaded.result_id == "run-2"
    assert loaded.items  # newest run keeps payload detail

    pool = await store.pool()
    async with pool.acquire() as conn:
        latency_count = await conn.fetchval(
            f"SELECT count(*) FROM {store.schema}.fetch_latency"
        )
        latest = await conn.fetchrow(
            f"SELECT result_id, envelope FROM {store.staging}.fetched_feed"
        )
        item_count = await conn.fetchval(
            f"SELECT count(*) FROM {store.staging}.fetched_feed_posting"
        )

    assert latency_count == 2
    assert latest["result_id"] == "run-2"
    assert json.loads(latest["envelope"]) == {"company": "Acme"}
    assert item_count == 1


async def test_schema_rename_preserves_history_and_can_be_repeated(store):
    await store.save(snapshot("before-rename"))
    old_schema = store.schema
    new_schema = f"{old_schema}_renamed"
    pool = await store.pool()
    async with pool.acquire() as conn, conn.transaction():
        await _rename_legacy_schema(conn, old_schema=old_schema, new_schema=new_schema)
    store.schema = new_schema  # The fixture drops the renamed schema on teardown.
    store.staging = new_schema

    async with pool.acquire() as conn, conn.transaction():
        await _rename_legacy_schema(conn, old_schema=old_schema, new_schema=new_schema)
        assert await conn.fetchval("SELECT to_regnamespace($1)", old_schema) is None
    await store.initialize()

    assert (await store.load_ref(*FEED)).result_id == "before-rename"
    assert [row["result_id"] for row in await store.run_history(REF)] == ["before-rename"]


async def test_schema_rename_refuses_to_hide_either_existing_history(store):
    await store.save(snapshot("keep-history"))
    new_schema = f"{store.schema}_existing"
    pool = await store.pool()
    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA {new_schema}")
        try:
            with pytest.raises(RuntimeError, match="Both .* schemas exist"):
                async with conn.transaction():
                    await _rename_legacy_schema(conn, old_schema=store.schema, new_schema=new_schema)
            assert await conn.fetchval("SELECT to_regnamespace($1)", store.schema) is not None
            assert await conn.fetchval("SELECT to_regnamespace($1)", new_schema) is not None
        finally:
            await conn.execute(f"DROP SCHEMA {new_schema} CASCADE")
    assert [row["result_id"] for row in await store.run_history(REF)] == ["keep-history"]


async def test_load_ref_returns_only_the_newest_run(store):
    await store.save(snapshot("run-old", finished_at=NOW))
    await store.save(
        snapshot(
            "run-new",
            finished_at=NOW + timedelta(minutes=1),
            items=({"external_id": "job-2", "kind": "json",
                    "payload": {"title": "Newest"}, "raw_document": {}},),
        )
    )

    loaded = await store.load_ref(*FEED)
    assert loaded is not None

    # Staged postings are replaced wholesale, so job-1 is gone with its run.
    # Carrying a payload across runs is item_version's job now, not a seed table's.
    assert loaded.items == (
        {"external_id": "job-2", "kind": "json", "payload": {"title": "Newest"},
         "raw_document": {}},
    )
    assert await store.load_ref("missing", "missing") is None


async def test_a_document_payload_round_trips_as_a_json_scalar(store):
    # html and xml adapters hand the store a raw document string rather than a
    # mapping, and only json items arrive pre-serialized as ``payload_json``.
    # Passing the string through untouched would reach jsonb as malformed JSON.
    document = '<!DOCTYPE html><body>{"not": json}</body>'
    await store.save(
        snapshot(
            "run-html",
            items=({"external_id": "job-1", "kind": "html",
                    "payload": document, "raw_document": {}},),
        )
    )

    loaded = await store.load_ref(*FEED)

    assert loaded is not None
    assert loaded.items[0]["payload"] == document


async def test_a_partial_rerun_replaces_the_staged_postings(store):
    await store.save(
        snapshot(
            "run-full",
            finished_at=NOW,
            items=(
                {"external_id": "job-1", "kind": "json",
                 "payload": {"title": "Old"}, "raw_document": {}},
                {"external_id": "job-2", "kind": "json",
                 "payload": {"title": "Keep"}, "raw_document": {}},
            ),
        )
    )
    await store.save(
        snapshot(
            "run-partial",
            finished_at=NOW + timedelta(minutes=1),
            status="partial",
            truncated=True,
            items=({"external_id": "job-1", "kind": "json",
                    "payload": {"title": "Refetched"}, "raw_document": {}},),
        )
    )

    loaded = await store.load_ref(*FEED)
    assert loaded is not None

    # A partial run stages what it got and nothing more: job-2's payload is not
    # carried over, so the next fetch pays for it again. That is the cost of
    # dropping the seed table, and it is deliberate.
    assert loaded.items == (
        {"external_id": "job-1", "kind": "json", "payload": {"title": "Refetched"},
         "raw_document": {}},
    )


async def test_load_ref_keeps_staged_items_after_a_newer_empty_error(store):
    await store.save(
        snapshot(
            "run-good",
            finished_at=NOW,
            items=({"external_id": "job-1", "kind": "json",
                    "payload": {"title": "Reusable"}, "raw_document": {}},),
        )
    )
    await store.save(
        snapshot(
            "run-error",
            finished_at=NOW + timedelta(minutes=1),
            status="error",
            error_class="NetError",
            items=(),
        )
    )

    loaded = await store.load_ref(*FEED)
    assert loaded is not None
    assert loaded.items == (
        {"external_id": "job-1", "kind": "json", "payload": {"title": "Reusable"},
         "raw_document": {}},
    )


async def test_history_keeps_only_latency_and_latest_network(store):
    await store.save(snapshot("run-a"))

    history = await store.run_history(REF)
    network = await store.run_network("run-a")

    assert history[0]["network_summary"] == {"retry_count": 2}
    assert history[0]["trigger"] == "manual"
    assert history[0]["parse"] == {"status": None, "summary": None}
    assert network == {"summary": {"request_count": 1, "retry_count": 2}, "requests": []}
    assert await store.run_network("missing") is None

    await store.save(snapshot("run-b", finished_at=NOW + timedelta(minutes=1)))
    assert await store.run_network("run-a") is None
    assert await store.run_network("run-b") == network


async def test_latest_validator_reports_staged_etag_without_the_payload(store):
    assert await store.latest_validator(*FEED) == (None, 0)

    await store.save(snapshot("run-a", response_etag='W/"v1"'))

    assert await store.latest_validator(*FEED) == ('W/"v1"', 1)


async def test_source_stats_aggregate_over_history(store):
    await store.save(snapshot("run-a", finished_at=NOW))
    await store.save(
        snapshot(
            "run-b",
            finished_at=NOW + timedelta(minutes=1),
            status="error",
            error_class="NetError",
        )
    )

    stats = await store.source_stats()

    assert len(stats) == 1
    row = stats[0]
    assert row["source"] == "lever"
    assert row["run_count"] == 2
    assert row["feed_count"] == 1
    assert row["error_count"] == 1
    assert row["retry_count"] == 4
    # Aggregates must stay JSON numbers: sum() over bigint yields numeric,
    # which reaches the API as a string unless it is cast back.
    assert row["raw_size_bytes"] == 36
    assert all(
        isinstance(row[column], int)
        for column in ("run_count", "raw_size_bytes", "retry_count")
    )
