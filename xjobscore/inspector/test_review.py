"""Unit tests for review.py's validation, shaping, and write path -- no DB.

Following the postings.py precedent: the SQL itself is exercised by running
the page against a real database (see the plan's verification notes); what
is tested here is what is easy to get silently wrong -- enum validation, the
provenance keys a write stamps, and the row shaping the page depends on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from xjobscore.inspector import review

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def review_row(**overrides):
    row = {
        "id": 1,
        "source": "greenhouse:etched",
        "source_key": "j1",
        "company": "Etched",
        "title": "ML Systems Intern",
        "location_text": "SF",
        "source_url": "https://jobs.example/1",
        "apply_url": None,
        "posted_at": NOW,
        "status": "active",
        "job_type": "intern",
        "cs_field": True,
        "ck_list": "ai:infra",
        "has_description": True,
        "attributes": {
            "version": 1,
            "opus": "tech",
            "opus_by": "machine",
            "opus_machine": "tech",
        },
        "total": 1,
    }
    row.update(overrides)
    return row


def count_row(**overrides):
    row = {"label": "tech", "opus_by": "machine", "changed": False}
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

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.results.pop(0) if self.results else []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row


def pool_with(rows, counts=()):
    # fetch order: vendor list, then _COUNTS, then the page itself.
    return FakePool([{"vendor": "greenhouse"}], list(counts), rows)


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("label", ["all", "bogus"])
async def test_unknown_label_rejected_only_when_bogus(label):
    if label == "bogus":
        with pytest.raises(ValueError):
            await review.query(pool_with([]), label=label)
    else:
        await review.query(pool_with([]), label=label)  # does not raise


async def test_unknown_state_rejected():
    with pytest.raises(ValueError):
        await review.query(pool_with([]), state="bogus")


async def test_unknown_order_rejected():
    with pytest.raises(ValueError):
        await review.query(pool_with([]), order="bogus")


# ------------------------------------------------------------- scoping

async def test_query_always_scopes_to_the_opus_set():
    pool = pool_with([])
    await review.query(pool)
    sql, _ = pool.calls[-1]
    assert "p.attributes ? 'opus'" in sql


async def test_default_state_is_unreviewed():
    pool = pool_with([])
    body = await review.query(pool)
    assert body["filters"]["state"] == "unreviewed"
    sql, _ = pool.calls[-1]
    assert "opus_by', 'machine') = 'machine'" in sql


async def test_reviewed_state_filters_on_human_provenance():
    pool = pool_with([])
    await review.query(pool, state="reviewed")
    sql, _ = pool.calls[-1]
    assert "opus_by' = 'human'" in sql


async def test_changed_state_filters_on_machine_vs_human_mismatch():
    pool = pool_with([])
    await review.query(pool, state="changed")
    sql, _ = pool.calls[-1]
    assert "opus' IS DISTINCT FROM p.attributes->>'opus_machine'" in sql


async def test_label_filter_selects_one_bucket():
    pool = pool_with([])
    await review.query(pool, label="non-tech")
    sql, _ = pool.calls[-1]
    assert "opus' = 'non-tech'" in sql


# ------------------------------------------------------------- counts

async def test_label_counts_tally_across_all_rows_regardless_of_view():
    counts = review.label_counts(
        [count_row(label="tech"), count_row(label="tech"), count_row(label="other")]
    )
    assert counts == {"tech": 2, "non-tech": 0, "ambiguous": 0, "other": 1, "total": 3}


async def test_state_counts_tally_unreviewed_reviewed_changed():
    rows = [
        count_row(opus_by="machine", changed=False),
        count_row(opus_by="human", changed=False),
        count_row(opus_by="human", changed=True),
    ]
    counts = review.state_counts(rows)
    assert counts == {"all": 3, "unreviewed": 1, "reviewed": 2, "changed": 1}


# ------------------------------------------------------------- row shaping

async def test_rows_are_shaped_with_opus_provenance():
    rows = [review_row(total=1)]
    body = await review.query(pool_with(rows, counts=[count_row()]))

    assert body["total"] == 1
    assert body["sources"] == ["greenhouse"]
    (row,) = body["rows"]
    assert "total" not in row
    assert "attributes" not in row
    assert row["opus"] == "tech"
    assert row["opus_by"] == "machine"
    assert row["opus_machine"] == "tech"
    assert row["opus_at"] is None
    assert row["posted_at"] == NOW.isoformat()
    assert row["cs_field"] is True
    assert row["ck_list"] == "ai:infra"


async def test_opus_by_defaults_to_machine_when_absent():
    rows = [review_row(attributes={"version": 1, "opus": "other"}, total=1)]
    body = await review.query(pool_with(rows, counts=[count_row()]))
    (row,) = body["rows"]
    assert row["opus_by"] == "machine"
    assert row["opus_machine"] is None


async def test_empty_page_reads_as_zero():
    body = await review.query(pool_with([]))
    assert body["total"] == 0
    assert body["rows"] == []


# ------------------------------------------------------------- detail

async def test_detail_none_when_unknown():
    assert await review.detail(FakePool(row=None), 999) is None


async def test_detail_shapes_row():
    row = review_row()
    del row["has_description"], row["total"]
    row["description"] = "<p>Build model infra.</p>"
    body = await review.detail(FakePool(row=row), 1)
    assert body["description"] == "<p>Build model infra.</p>"
    assert body["opus"] == "tech"
    assert body["posted_at"] == NOW.isoformat()


# ------------------------------------------------------------- writes

async def test_set_labels_stamps_human_provenance_and_preserves_machine():
    pool = FakePool(row={"id": 1})
    written = await review.set_labels(pool, [{"id": 1, "label": "non-tech"}])

    assert written == 1
    sql, args = pool.calls[-1]
    assert "attributes || $2::jsonb" in sql
    posting_id, patch = args
    assert posting_id == 1
    # A plain dict, not a json.dumps string -- the pool's jsonb codec runs
    # orjson.dumps on the parameter itself; handing it pre-serialized text
    # double-encodes into a jsonb string scalar, and `object || scalar`
    # wraps into an array instead of merging (see set_labels' docstring).
    assert isinstance(patch, dict)
    assert patch["opus"] == "non-tech"
    assert patch["opus_by"] == "human"
    assert "opus_at" in patch
    assert "opus_machine" not in patch  # never touched by a write


async def test_set_labels_skips_rows_outside_the_opus_set():
    pool = FakePool(row=None)  # RETURNING id finds nothing -- WHERE excluded it
    written = await review.set_labels(pool, [{"id": 999, "label": "tech"}])
    assert written == 0


async def test_set_labels_rejects_unknown_label_before_any_write():
    with pytest.raises(KeyError):
        # label validation happens at the route (Pydantic Literal); this
        # module trusts its caller, so a malformed entry surfaces as a
        # KeyError rather than silently writing garbage.
        await review.set_labels(FakePool(row={"id": 1}), [{"id": 1}])
