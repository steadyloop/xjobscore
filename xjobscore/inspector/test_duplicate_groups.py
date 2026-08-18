"""Unit tests for duplicate-group paging and review shaping without PostgreSQL."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

import pytest

from xjobscore.inspector import duplicate_groups

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakePool:
    def __init__(self, *, fetches=(), row=None, value=None):
        self.fetches = list(fetches)
        self.row = row
        self.value = value
        self.calls: list[tuple[str, str, tuple]] = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetches.pop(0) if self.fetches else []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.value


def evidence():
    return {
        "strategy_version": 1,
        "keyer": "company_role",
        "key": "1:software engineer",
        "members": [
            {
                "posting_id": 11,
                "source": "ashby:acme",
                "location": "new york",
                "ats_identity": "ashby:acme/one",
                "description_hash": "aaa",
            },
            {
                "posting_id": 12,
                "source": "greenhouse:acme",
                "location": "remote",
                "ats_identity": "greenhouse:acme/two",
                "description_hash": "bbb",
            },
        ],
    }


def group_row(**overrides):
    row = {
        "id": GROUP_ID,
        "fingerprint": "current",
        "algorithm_state": "pending",
        "evidence": evidence(),
        "company": "Acme",
        "title": "Software Engineer",
        "representative_posting_id": 12,
        "member_count": 2,
        "sources": ["ashby", "greenhouse"],
        "locations": ["New York", "Remote"],
        "latest_seen_at": NOW,
        "review_decision": "correct",
    }
    row.update(overrides)
    return row


def member_row(posting_id, **overrides):
    row = {
        "dup_group_id": GROUP_ID,
        "id": posting_id,
        "source": "ashby:acme" if posting_id == 11 else "greenhouse:acme",
        "source_key": f"job-{posting_id}",
        "company": "Acme",
        "title": "Software Engineer",
        "location_text": "New York" if posting_id == 11 else "Remote",
        "source_url": f"https://jobs.example/{posting_id}",
        "apply_url": None,
        "posted_at": NOW,
        "status": "active",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "has_description": True,
    }
    row.update(overrides)
    return row


async def test_query_pages_complete_groups_and_merges_current_review():
    reviews = {
        (str(GROUP_ID), "current"): {
            "decision": "correct",
            "note": "same opening",
            "reviewed_at": NOW.isoformat(),
        }
    }
    pool = FakePool(
        fetches=[
            [{"vendor": "ashby"}, {"vendor": "greenhouse"}],
            [group_row()],
            [member_row(11), member_row(12)],
        ],
        row={"all": 3, "unreviewed": 2, "correct": 1, "incorrect": 0, "needs_review": 0, "hidden": 0},
    )

    body = await duplicate_groups.query(
        pool, reviews, review_state="correct", limit=1, offset=0
    )

    assert body["total"] == 1
    assert body["review_counts"] == {
        "all": 3,
        "unreviewed": 2,
        "correct": 1,
        "incorrect": 0,
        "needs_review": 0,
        "hidden": 0,
    }
    (group,) = body["groups"]
    assert group["review"]["decision"] == "correct"
    assert group["changed_since_review"] is False
    assert group["warnings"] == [
        "location_mismatch",
        "ats_identity_mismatch",
        "description_hash_mismatch",
    ]
    assert [member["id"] for member in group["members"]] == [11, 12]
    assert group["members"][0]["evidence"]["description_hash"] == "aaa"
    assert group["members"][0]["posted_at"] == NOW.isoformat()

    page_sql = next(sql for kind, sql, _ in pool.calls if "FROM base" in sql and "LIMIT" in sql)
    member_sql = next(sql for kind, sql, _ in pool.calls if "p.dup_group_id = ANY" in sql)
    assert "p.description," not in member_sql.lower()
    assert "review_decision = 'correct'" in page_sql


async def test_changed_fingerprint_is_unreviewed_but_history_is_retained():
    reviews = {
        (str(GROUP_ID), "old"): {
            "decision": "correct",
            "note": None,
            "reviewed_at": NOW.isoformat(),
        }
    }
    pool = FakePool(
        fetches=[[{"vendor": "ashby"}], [group_row(review_decision=None)], []],
        row={"all": 1, "unreviewed": 1, "correct": 0, "incorrect": 0, "needs_review": 0, "hidden": 0},
    )

    body = await duplicate_groups.query(pool, reviews)

    (group,) = body["groups"]
    assert group["review"] is None
    assert group["changed_since_review"] is True


async def test_empty_page_keeps_filtered_total_from_counts():
    pool = FakePool(
        fetches=[[{"vendor": "ashby"}], []],
        row={"all": 5, "unreviewed": 3, "correct": 2, "incorrect": 0, "needs_review": 0, "hidden": 4},
    )
    body = await duplicate_groups.query(pool, {}, offset=50)
    assert body["total"] == 3
    assert body["groups"] == []


async def test_hidden_groups_have_a_recoverable_queue_but_are_excluded_from_all():
    reviews = {
        (str(GROUP_ID), "current"): {
            "decision": "hidden",
            "note": None,
            "reviewed_at": NOW.isoformat(),
        }
    }
    pool = FakePool(
        fetches=[[{"vendor": "ashby"}], [group_row(review_decision="hidden")], []],
        row={
            "all": 4,
            "unreviewed": 3,
            "correct": 1,
            "incorrect": 0,
            "needs_review": 0,
            "hidden": 2,
        },
    )

    body = await duplicate_groups.query(pool, reviews, review_state="hidden")

    assert body["total"] == 2
    assert body["groups"][0]["review"]["decision"] == "hidden"
    page_sql = next(
        sql for kind, sql, _ in pool.calls if "FROM base" in sql and "LIMIT" in sql
    )
    assert "review_decision = 'hidden'" in page_sql
    assert duplicate_groups._REVIEW_PREDICATES["all"] == (
        "review_decision IS DISTINCT FROM 'hidden'"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"review_state": "pending"}, "unknown review state"),
        ({"order": "largest"}, "unknown order"),
    ],
)
async def test_unknown_filters_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        await duplicate_groups.query(FakePool(), {}, **kwargs)


async def test_random_order_generates_and_echoes_seed():
    pool = FakePool(
        fetches=[[], []],
        row={"all": 0, "unreviewed": 0, "correct": 0, "incorrect": 0, "needs_review": 0, "hidden": 0},
    )
    body = await duplicate_groups.query(pool, {}, order="random")
    assert body["filters"]["seed"]
    page_call = next(call for call in pool.calls if "ORDER BY md5" in call[1])
    assert body["filters"]["seed"] in page_call[2]


async def test_current_fingerprint_reads_gold_evidence_hash():
    pool = FakePool(value="abc123")
    assert await duplicate_groups.current_fingerprint(pool, GROUP_ID) == "abc123"
    _, sql, args = pool.calls[-1]
    assert "md5(evidence::text)" in sql
    assert args == (GROUP_ID,)
