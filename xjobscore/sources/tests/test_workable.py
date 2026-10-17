"""Workable consolidation preserves transport, content and replay contracts."""

from copy import deepcopy
from dataclasses import fields, replace
import json

import pytest

from xjobscore import router, silver, sources
from xjobscore.net import NetError, Response
from xjobscore.records import Feed, Item, Record
from xjobscore.sources import json_api, workable

FEED = Feed(1, "workable", "example", interval_s=86400)
ENVELOPE = {"name": "Example", "description": "About Example"}
ROWS_KEY = "_workable_location_rows"


def row(city="Boston", state="Massachusetts", country="United States", **changes):
    return {
        "shortcode": "ABC", "title": "Engineer", "description": "<p>Build things.</p>",
        "url": "https://apply.workable.com/example/j/ABC/", "code": "REQ-1",
        "application_url": "https://apply.workable.com/example/j/ABC/apply/",
        "employment_type": "Full-time", "telecommuting": True,
        "published_on": "2026-10-01", "created_at": "2026-09-30",
        "city": city, "state": state, "country": country,
        "locations": [{"city": city, "region": state, "country": country}],
        **changes,
    }


class FixtureNet:
    def __init__(self, rows, status=200):
        self.response = Response(
            url="https://fixture.invalid", status=status, headers={},
            body=json.dumps({**ENVELOPE, "jobs": rows}).encode(),
        )
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


async def harvest(rows):
    return await workable.harvest(FEED, FixtureNet(rows))


async def test_locations_become_one_posting_with_verbatim_rows_and_final_primary(monkeypatch):
    rows = [row(), row("Paris", "", "France"), row("Toronto", "Ontario", "Canada")]
    originals = deepcopy(rows)
    net, generic_net = FixtureNet(rows), FixtureNet(rows)
    record = await workable.harvest(FEED, net)
    generic = await json_api.harvest("workable", FEED, generic_net)
    assert net.calls == generic_net.calls and len(net.calls) == 1
    assert len(record.items) == 1
    item = record.items[0]
    assert item.external_id == "ABC"
    assert item.payload[ROWS_KEY] == {"version": 1, "rows": originals}
    assert {k: v for k, v in item.payload.items() if k != ROWS_KEY} == rows[-1]
    assert rows == originals
    for field in fields(Record):
        if field.name != "items":
            assert getattr(record, field.name) == getattr(generic, field.name)

    original_parse = json_api.parse
    calls = []
    def counted_parse(*args):
        calls.append(args)
        return original_parse(*args)
    monkeypatch.setattr(json_api, "parse", counted_parse)
    postings = workable.parse(FEED, item, record.envelope)
    assert len(calls) == len(postings) == 1
    posting = postings[0]
    final = original_parse("workable", FEED, generic.items[-1], generic.envelope)[0]
    for field in fields(posting):
        if field.name not in {"locations_extra", "location_normalization_extra"}:
            assert getattr(posting, field.name) == getattr(final, field.name)
    assert posting.location_raw == "Toronto, Ontario, Canada"
    assert posting.locations_extra == ["Boston", "Paris", "Toronto"]
    normalized = silver._normalized_locations(posting)[1]
    assert {loc["country"] for loc in normalized} == {"US", "FR", "CA"}
    for original in generic.items:
        old = original_parse("workable", FEED, original, generic.envelope)[0]
        assert all(loc in normalized for loc in silver._normalized_locations(old)[1])


@pytest.mark.parametrize("rows", [[], [row()], [row(city="", state="", locations=[])]])
async def test_empty_and_single_rows_are_unchanged(rows):
    record = await harvest(rows)
    generic = await json_api.harvest("workable", FEED, FixtureNet(rows))
    assert record == generic
    for item in record.items:
        assert workable.parse(FEED, item, record.envelope) == json_api.parse(
            "workable", FEED, item, record.envelope,
        )


@pytest.mark.parametrize("changes", [
    {"description": "Edited description"}, {"title": "Senior Engineer"},
    {"telecommuting": False}, {"code": "REQ-2"},
    {"unknown_vendor_field": "new"}, {"published_on": "2026-10-02"},
    {"telecommuting": 1},
])
async def test_conflicting_fields_fall_back_in_original_order(changes):
    rows = [row(), row(shortcode="OTHER"), row("Paris", "", "France", **changes)]
    record = await harvest(rows)
    generic = await json_api.harvest("workable", FEED, FixtureNet(rows))
    assert record == generic
    assert [workable.parse(FEED, item, record.envelope) for item in record.items] == [
        json_api.parse("workable", FEED, item, record.envelope) for item in generic.items
    ]


async def test_safe_groups_emit_at_last_occurrence_and_keep_duplicate_rows():
    first = row()
    rows = [first, row(shortcode="OTHER"), first, row("Paris", "", "France")]
    record = await harvest(rows)
    assert [item.external_id for item in record.items] == ["OTHER", "ABC"]
    assert record.items[-1].payload[ROWS_KEY]["rows"] == [first, first, rows[-1]]


async def test_description_edits_and_location_removal_change_snapshots():
    initial = await harvest([row(), row("Paris", "", "France")])
    edited = await harvest([
        row(description="Edited"), row("Paris", "", "France", description="Edited"),
    ])
    removed = await harvest([row("Paris", "", "France")])
    hashes = {router.snapshot_hash(router.prepare(FEED, record)) for record in (initial, edited, removed)}
    assert len(hashes) == 3
    assert workable.parse(FEED, edited.items[0], edited.envelope)[0].description_html == "Edited"
    posting = workable.parse(FEED, removed.items[0], removed.envelope)[0]
    assert {loc["country"] for loc in silver._normalized_locations(posting)[1]} == {"FR"}


async def test_historical_and_consolidated_payloads_replay_and_generic_parser_reads_root():
    record = await harvest([row(), row("Paris", "", "France")])
    for item in [Item("ABC", row()), record.items[0]]:
        wrapped = router.prepare(FEED, Record(items=[item], envelope=ENVELOPE))[0].data
        replayed, envelope = router.unwrap(json.loads(json.dumps(wrapped)))
        replayed = replace(replayed, external_id=item.external_id)
        assert workable.parse(FEED, replayed, envelope) == workable.parse(FEED, item, ENVELOPE)
        generic = json_api.parse("workable", FEED, replayed, envelope)[0]
        assert generic.title == "Engineer" and generic.description_html == "<p>Build things.</p>"
        assert generic.external_id == "ABC"


async def test_all_record_diagnostics_survive_consolidation(monkeypatch):
    original = Record(
        items=[Item("ABC", row()), Item("ABC", row("Paris", "", "France"))],
        envelope=ENVELOPE, truncated=True, http_status=206, etag='"abc"',
        last_modified="yesterday", count_unchanged=True, list_total=10,
        full_listing=True, reused_item_count=2, enumerated_item_count=10,
        partition_count=3, detail_fetched_count=4, detail_skipped_count=5,
        result_window_truncated=True,
    )
    async def fake_harvest(*args):
        return original
    monkeypatch.setattr(json_api, "harvest", fake_harvest)
    record = await workable.harvest(FEED, None)
    assert len(record.items) == 1
    assert replace(record, items=original.items) == original


async def test_failed_requests_propagate_unchanged(monkeypatch):
    error = NetError("fixture failure", url="https://fixture.invalid", status=429)
    async def fail(*args):
        raise error
    monkeypatch.setattr(json_api, "harvest", fail)
    with pytest.raises(NetError) as caught:
        await workable.harvest(FEED, None)
    assert caught.value is error


@pytest.mark.parametrize("metadata", [None, {"version": 2, "rows": []}, {"version": 1, "rows": ["invalid"]}])
def test_unknown_metadata_replays_as_ordinary_payload(metadata):
    item = Item("ABC", row(**{ROWS_KEY: metadata}))
    assert workable.parse(FEED, item, ENVELOPE) == json_api.parse("workable", FEED, item, ENVELOPE)


async def test_reserved_metadata_and_probe_rows_are_not_consolidated():
    rows = [row(**{ROWS_KEY: {"vendor": True}}), row(**{ROWS_KEY: {"vendor": True}})]
    assert (await harvest(rows)) == await json_api.harvest("workable", FEED, FixtureNet(rows))
    probe = Item("ABC", {"_probe": {"state": "closed", "url": "https://example.test"}})
    assert workable.parse(FEED, probe, ENVELOPE) == json_api.parse("workable", FEED, probe, ENVELOPE)


def test_only_workable_registration_changes():
    assert sources.get("workable") is workable.harvest
    assert sources.parser("workable") is workable.parse
    assert sources.reusing("workable", {}) is workable.harvest
    assert sources.width("workable") == 1
    for name in json_api.CONFIGS:
        if name == "workable":
            continue
        registered = sources.get(name)
        if getattr(registered, "func", None) is json_api.harvest:
            assert registered.args == (name,)
            assert sources.parser(name).func is json_api.parse
            assert sources.parser(name).args == (name,)


async def test_consolidated_locations_and_edits_survive_database_pipeline(core_schema):
    from datetime import datetime, timedelta, timezone
    from xjobscore import run, store

    now = datetime.now(timezone.utc)
    pool = core_schema.pool
    async with pool.acquire() as conn:
        company_id = await conn.fetchval("INSERT INTO company(name) VALUES('Example') RETURNING id")
        await conn.execute(
            "INSERT INTO token(ats, token, company_id, status) VALUES('workable', 'example', $1, 'live')",
            company_id,
        )
        feed_id = await conn.fetchval(
            "INSERT INTO feed(source, key, polling, interval_s, due_at) VALUES('workable', 'example', true, 86400, $1) RETURNING id",
            now,
        )
        claimed = await store.claim_due_feeds(conn, now, 1)
    feed = claimed[0]
    cases = [
        [row(), row('Paris', '', 'France')],
        [row(description='Edited'), row('Paris', '', 'France', description='Edited')],
        [row('Paris', '', 'France', description='Edited')],
    ]
    posting_id = None
    for index, rows in enumerate(cases):
        poll_at = now + timedelta(days=index)
        if index:
            async with pool.acquire() as conn:
                await conn.execute('UPDATE feed SET due_at=$1, hold_until=NULL WHERE id=$2', poll_at, feed_id)
                claimed = await store.claim_due_feeds(conn, poll_at, 1)
                feed = claimed[0]
        assert await run.poll(pool, FixtureNet(rows), feed, poll_at) == 'ok'
        async with pool.acquire() as conn:
            jobs = await conn.fetch('SELECT id, source_key, description, location_text, locations FROM job_posting')
            latest_raw = await conn.fetchval('SELECT data FROM raw_posting ORDER BY id DESC LIMIT 1')
            interval = await conn.fetchval('SELECT interval_s FROM feed WHERE id=$1', feed_id)
        assert len(jobs) == 1 and jobs[0]['source_key'] == 'ABC'
        posting_id = posting_id or jobs[0]['id']
        assert jobs[0]['id'] == posting_id and interval == 86400
        assert jobs[0]['location_text'] == 'Paris, France'
        assert jobs[0]['description'] == ('Build things.' if index == 0 else 'Edited')
        assert {loc['country'] for loc in jobs[0]['locations']} == ({'US', 'FR'} if index < 2 else {'FR'})
        if index < 2:
            assert latest_raw['payload'][ROWS_KEY] == {'version': 1, 'rows': rows}
        else:
            assert latest_raw['payload'] == rows[0]


@pytest.mark.parametrize('locations', [None, {}, 'unknown', 3])
async def test_non_list_structured_locations_keep_generic_parser_compatibility(locations):
    record = await harvest([row(locations=locations), row('Paris', '', 'France', locations=locations)])
    posting = workable.parse(FEED, record.items[0], record.envelope)[0]
    assert posting.location_raw == 'Paris, France'
    assert posting.locations_extra == []
    assert {loc['country'] for loc in silver._normalized_locations(posting)[1]} == {'US', 'FR'}
