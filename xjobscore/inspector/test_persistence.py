"""ResultStore behavior: latest payload staging and latency history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from xjobscore.feed_identity import feed_ref
from xjobscore.inspector.cache import RunSnapshot

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


async def test_load_latest_items_ref_returns_only_the_newest_run(store):
    await store.save(snapshot("run-old", finished_at=NOW))
    await store.save(
        snapshot(
            "run-new",
            finished_at=NOW + timedelta(minutes=1),
            items=({"external_id": "job-2", "kind": "json",
                    "payload": {"title": "Newest"}, "raw_document": {}},),
        )
    )

    items = await store.load_latest_items_ref(*FEED)

    # Staged postings are replaced wholesale, so job-1 is gone with its run.
    # Carrying a payload across runs is item_version's job now, not a seed table's.
    assert items == (
        {"external_id": "job-2", "kind": "json", "payload": {"title": "Newest"}},
    )
    assert await store.load_latest_items_ref("missing", "missing") == ()


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
    assert await store.load_latest_items_ref(*FEED) == (
        {"external_id": "job-1", "kind": "html", "payload": document},
    )


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

    items = await store.load_latest_items_ref(*FEED)

    # A partial run stages what it got and nothing more: job-2's payload is not
    # carried over, so the next fetch pays for it again. That is the cost of
    # dropping the seed table, and it is deliberate.
    assert items == (
        {"external_id": "job-1", "kind": "json", "payload": {"title": "Refetched"}},
    )


async def test_load_latest_items_ref_skips_a_newer_empty_error(store):
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

    assert await store.load_latest_items_ref(*FEED) == (
        {"external_id": "job-1", "kind": "json", "payload": {"title": "Reusable"}},
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


async def test_meta_round_trip(store):
    assert await store.get_meta("some.key", "fallback") == "fallback"
    await store.set_meta("some.key", "value")
    assert await store.get_meta("some.key", "fallback") == "value"


async def test_posting_labels_round_trip(store):
    key = ("lever:acme", "job-1")
    assert await store.posting_labels() == {}

    written = await store.set_posting_labels([
        {
            "source": key[0],
            "source_key": key[1],
            "label": "unsure",
            "note": None,
            "title_snapshot": "Demo",
            "company_snapshot": "Acme",
        }
    ])
    assert written == 1
    labels = await store.posting_labels()
    assert labels[key]["label"] == "unsure"
    first_stamp = labels[key]["labeled_at"]

    # A relabel overwrites in place and re-stamps labeled_at.
    await store.set_posting_labels([
        {"source": key[0], "source_key": key[1], "label": "relevant", "note": "ml"}
    ])
    labels = await store.posting_labels()
    assert labels[key]["label"] == "relevant"
    assert labels[key]["note"] == "ml"
    assert labels[key]["labeled_at"] >= first_stamp


async def test_posting_labels_delete_ignores_unknown_keys(store):
    await store.set_posting_labels([
        {"source": "lever:acme", "source_key": "job-1", "label": "irrelevant"}
    ])

    deleted = await store.delete_posting_labels(
        [("lever:acme", "job-1"), ("lever:acme", "never-labeled")]
    )

    assert deleted == 1
    assert await store.posting_labels() == {}
    assert await store.delete_posting_labels([]) == 0
    assert await store.set_posting_labels([]) == 0


async def test_duplicate_group_reviews_retain_versions_and_clear_current(store):
    group_id = "00000000-0000-0000-0000-000000000001"
    await store.create_schema()  # existing Inspector schemas upgrade idempotently
    await store.create_schema()
    assert await store.duplicate_group_reviews() == {}

    await store.set_duplicate_group_review(
        group_id=group_id,
        fingerprint="first",
        decision="correct",
        note="same role",
    )
    await store.set_duplicate_group_review(
        group_id=group_id,
        fingerprint="second",
        decision="needs_review",
    )
    reviews = await store.duplicate_group_reviews()
    assert reviews[(group_id, "first")]["decision"] == "correct"
    assert reviews[(group_id, "first")]["note"] == "same role"
    assert reviews[(group_id, "second")]["decision"] == "needs_review"
    assert reviews[(group_id, "second")]["reviewed_at"] is not None

    await store.set_duplicate_group_review(
        group_id=group_id,
        fingerprint="second",
        decision="incorrect",
        note="different locations",
    )
    reviews = await store.duplicate_group_reviews()
    assert reviews[(group_id, "second")]["decision"] == "incorrect"
    assert reviews[(group_id, "second")]["note"] == "different locations"

    assert await store.delete_duplicate_group_review(
        group_id=group_id, fingerprint="second"
    ) == 1
    assert (group_id, "second") not in await store.duplicate_group_reviews()
    assert (group_id, "first") in await store.duplicate_group_reviews()
    assert await store.delete_duplicate_group_review(
        group_id=group_id, fingerprint="missing"
    ) == 0
