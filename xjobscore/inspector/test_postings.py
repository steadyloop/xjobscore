"""Unit tests for postings.py's validation, shaping, and label merge -- no DB.

Following the telemetry precedent: the SQL itself is exercised by running the
page against a real database; what is tested here is what is easy to get
silently wrong -- enum validation, the parallel label arrays handed to the
pool, seed stability for the shuffle order, and the row shaping the page
depends on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

import pytest

from xjobscore.inspector import postings

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

LABELS = {
    ("ashby:acme", "j1"): {"label": "relevant", "note": "ml intern", "labeled_at": "2026-08-14T00:00:00+00:00"},
    ("ashby:acme", "j2"): {"label": "irrelevant", "note": None, "labeled_at": "2026-08-14T00:00:00+00:00"},
    ("ashby:zed", "j9"): {"label": "relevant", "note": None, "labeled_at": "2026-08-14T00:00:00+00:00"},
}


def posting_row(**overrides):
    row = {
        "id": 1,
        "source": "ashby:acme",
        "source_key": "j1",
        "company": "Acme",
        "title": "ML Intern",
        "location_text": "NYC",
        "source_url": "https://jobs.example/1",
        "apply_url": None,
        "posted_at": NOW,
        "status": "active",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "attributes": {"version": 1},
        "dup_group_id": None,
        "has_description": True,
        "total": 1,
    }
    row.update(overrides)
    return row


class FakePool:
    """asyncpg.Pool stand-in recording every query and its arguments."""

    def __init__(self, *results, row=None):
        self.results = list(results)
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.results.pop(0) if self.results else []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row


def pool_with(rows):
    # First fetch is the vendor list, second the page itself.
    return FakePool([{"vendor": "ashby"}], rows)


# ------------------------------------------------------------- validation

async def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        await postings.query(pool_with([]), {}, status="bogus")


async def test_unknown_label_state_rejected():
    with pytest.raises(ValueError):
        await postings.query(pool_with([]), {}, label_state="bogus")


async def test_unknown_order_rejected():
    with pytest.raises(ValueError):
        await postings.query(pool_with([]), {}, order="bogus")


# ------------------------------------------------------------- shuffle seed

async def test_random_order_generates_and_echoes_seed():
    body = await postings.query(pool_with([]), {}, order="random")
    seed = body["filters"]["seed"]
    assert seed  # generated

    pool = pool_with([])
    body = await postings.query(pool, {}, order="random", seed=seed)
    assert body["filters"]["seed"] == seed  # a caller's seed is kept verbatim
    _, args = pool.calls[-1]
    assert seed in args  # and reaches the ORDER BY parameter


async def test_non_random_order_has_no_seed():
    body = await postings.query(pool_with([]), {}, order="newest")
    assert body["filters"]["seed"] == ""


# ------------------------------------------------------------- label plumbing

async def test_label_arrays_reach_the_query_in_key_order():
    pool = pool_with([])
    await postings.query(pool, LABELS)
    _, args = pool.calls[-1]
    sources, keys, values = args[0], args[1], args[2]
    assert list(zip(sources, keys)) == sorted(LABELS)
    assert values == [LABELS[key]["label"] for key in sorted(LABELS)]


async def test_label_counts_tally():
    counts = postings.label_counts(LABELS)
    assert counts == {"relevant": 2, "irrelevant": 1, "unsure": 0, "total": 3}


# ------------------------------------------------------------- row shaping

async def test_rows_are_shaped_and_labels_merged():
    rows = [
        posting_row(total=2),
        posting_row(
            id=2, source_key="j3", title="Chef", posted_at=None,
            attributes='{"version": 1, "opportunity_type": "summer_internship"}',
            dup_group_id=UUID("00000000-0000-0000-0000-000000000001"),
            total=2,
        ),
    ]
    body = await postings.query(pool_with(rows), LABELS)

    assert body["total"] == 2
    assert body["sources"] == ["ashby"]
    labeled, unlabeled = body["rows"]
    assert "total" not in labeled
    assert labeled["label"] == "relevant"
    assert labeled["note"] == "ml intern"
    assert labeled["posted_at"] == NOW.isoformat()
    assert unlabeled["label"] is None
    assert unlabeled["posted_at"] is None
    assert unlabeled["attributes"]["opportunity_type"] == "summer_internship"
    assert unlabeled["dup_group_id"] == "00000000-0000-0000-0000-000000000001"


async def test_empty_page_reads_as_zero():
    body = await postings.query(pool_with([]), {})
    assert body["total"] == 0
    assert body["rows"] == []


# ------------------------------------------------------------- detail

async def test_detail_none_when_unknown():
    assert await postings.detail(FakePool(row=None), {}, 999) is None


async def test_detail_shapes_and_merges_label():
    row = posting_row()
    del row["has_description"], row["total"]
    row.update(description="<p>Great job</p>", content_hash="abc", raw_id=7)
    body = await postings.detail(FakePool(row=row), LABELS, 1)
    assert body["description"] == "<p>Great job</p>"
    assert body["label"] == "relevant"
    assert body["first_seen_at"] == NOW.isoformat()
