"""Closure charts count postings once and preserve unknown observations."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from xjobscore.inspector import closures
from xjobscore.inspector.app import create_app

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def posting(**changes):
    row = dict(id=1, source="lever:acme", company="Acme", title="Engineer",
               first_seen_at=NOW - timedelta(days=2), last_seen_at=NOW - timedelta(hours=4),
               missing_since=NOW - timedelta(hours=3), ats_closed_at=None, linked_dead_at=None,
               paged_closed_at=NOW - timedelta(hours=1), detail="job page redirected")
    row.update(changes)
    return closures.prepare(row)


def snapshot(rows, backlog=()):
    return dict(observed_at=NOW, history_since=NOW - timedelta(days=2), closed=rows, backlog=backlog)


def test_overlapping_signals_count_once_and_first_evidence_wins():
    row = posting(ats_closed_at=NOW - timedelta(hours=2), linked_dead_at=NOW)
    result = closures.summary(snapshot([row]), 7, "UTC")
    assert result["evidence"] == {"ats": 1}
    assert result["metrics"]["closed"] == 1
    assert sum(sum(day["counts"].values()) for day in result["daily"]) == 1
    assert result["metrics"]["median_hours"] == 1


def test_no_history_is_fabricated_and_local_days_are_used():
    data = snapshot([posting(paged_closed_at=NOW.replace(hour=1))])
    result = closures.summary(data, 30, "America/Los_Angeles")
    assert len(result["daily"]) == 3
    assert result["daily"][1]["day"] == "2026-10-06"
    assert result["daily"][1]["counts"]["redirect"] == 1


def test_unknown_closure_dates_and_negative_lags_are_excluded():
    result = closures.summary(snapshot([
        posting(paged_closed_at=None), posting(missing_since=NOW)
    ]), 7, "UTC")
    assert result["metrics"]["undated"] == 1
    assert result["metrics"]["closed"] == 1
    assert result["metrics"]["lag_sample"] == 0
    assert result["metrics"]["p95_hours"] is None


def test_review_requires_current_listing_after_page_or_link_closure():
    assert posting(missing_since=None, last_seen_at=NOW)["review"]
    assert not posting(last_seen_at=NOW)["review"]
    assert not posting(missing_since=None, last_seen_at=NOW, ats_closed_at=NOW)["review"]


def test_backlog_does_not_change_with_chart_period():
    data = snapshot([], [dict(vendor="workday", outcome="inconclusive", age=4, count=10),
                         dict(vendor="lever", outcome="live", age=0, count=2)])
    for days in (1, 7):
        result = closures.summary(data, days, "UTC")
        assert result["metrics"]["unresolved"] == 12
        assert result["metrics"]["over_24h"] == 10
        assert result["outcomes"] == {"inconclusive": 10, "live": 2}


async def test_chart_selection_and_search_match_closed_postings():
    data = snapshot([posting(), posting(id=2, source="ashby:other", title="Intern")])
    result = await closures.postings(None, data, days=7, tz="UTC", view="closed",
                                     vendor="lever", reason="redirect", age=1, q="ACME")
    assert result["total"] == 1
    assert result["rows"][0]["id"] == 1


def test_page_and_unavailable_database_are_distinct_from_empty_data():
    class Policy:
        async def pool_or_none(self):
            return None

    async def noop():
        pass

    runtime = SimpleNamespace(result_store=None, policy=Policy(), catalog=None,
                              inspector=None, scheduler=None, heartbeat=None, start=noop, stop=noop)
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/closures").status_code == 200
        assert client.get("/api/closures").status_code == 503
        assert client.get("/api/closures/postings").status_code == 503
        assert client.get("/api/closures?tz=not/a/timezone").status_code == 400
        assert client.get("/api/closures?days=0").status_code == 422
        assert client.get("/api/closures/postings?age=5").status_code == 422


async def test_sql_reads_current_episode_and_replayable_evidence(core_schema):
    pool = core_schema.pool
    async with pool.acquire() as conn:
        company = await conn.fetchval("INSERT INTO company(name) VALUES('Acme') RETURNING id")
        for key in ("redirect", "missing"):
            raw = await conn.fetchval("""INSERT INTO raw_posting(source,source_key,data,data_hash)
                VALUES('lever:acme',$1,'{}',$1) RETURNING id""", key)
            await conn.execute("""INSERT INTO job_posting(source,source_key,company_id,title,content_hash,raw_id,
                first_seen_at,last_seen_at,missing_since) VALUES('lever:acme',$1,$2,'Engineer',$1,$3,$4,$4,$5)""",
                key, company, raw, NOW - timedelta(days=2), NOW - timedelta(days=1))
        await conn.execute("""UPDATE job_posting SET status='closed',paged_closed_at=$1 WHERE source_key='redirect'""", NOW)
        await conn.execute("""INSERT INTO raw_posting(source,source_key,data,data_hash) VALUES('lever:acme','redirect',
            '{"payload":{"_probe":{"outcome":"page_closed","detail":"job page redirected","http_status":200}}}', 'closure')""")
        await conn.execute("""INSERT INTO reconcile_queue(posting_id,missing_since,last_outcome)
            SELECT id,missing_since,'inconclusive' FROM job_posting WHERE source_key='missing'""")
        await conn.execute("""INSERT INTO reconcile_queue(posting_id,missing_since,last_outcome,finished_at)
            SELECT id,missing_since-interval '1 day','live',$1 FROM job_posting WHERE source_key='missing'""", NOW)
    analytics = closures.Analytics()
    data = await analytics.snapshot(pool)
    assert len(data["closed"]) == 1
    assert data["closed"][0]["reason"] == "redirect"
    assert sum(row["count"] for row in data["backlog"]) == 1
    assert data["backlog"][0]["outcome"] == "inconclusive"
    assert await analytics.snapshot(pool) is data
    result = await closures.postings(pool, data, days=7, tz="UTC", view="unresolved", outcome="live")
    assert result["total"] == 0
    result = await closures.postings(pool, data, days=7, tz="UTC", view="unresolved", outcome="inconclusive")
    assert result["total"] == 1
    assert result["rows"][0]["title"] == "Engineer"
