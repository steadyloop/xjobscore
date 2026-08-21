"""Gold-layer enrichment and dedup behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from xjobscore import gold


NOW = datetime(2026, 8, 17, 21, 34, tzinfo=timezone.utc)


def _row(posting_id: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": posting_id,
        "company_id": 1,
        "source": "greenhouse:acme",
        "title": "Software Engineer",
        "location_text": None,
        "description": None,
        "source_url": None,
        "apply_url": None,
    }
    row.update(overrides)
    return row


# Keying -------------------------------------------------------------------


def test_same_company_title_and_missing_location_group_together() -> None:
    plan = gold.plan_dedup(
        [_row(1, title="Software Engineer"), _row(2, title="software   engineer!")]
    )
    assert plan.counts == gold.Counts(groups=1, postings=2)
    assert {member["group_id"] for member in plan.members} == {plan.groups[0]["id"]}


def test_different_locations_never_group() -> None:
    plan = gold.plan_dedup(
        [_row(1, location_text="New York, NY"), _row(2, location_text="San José, CA")]
    )
    assert plan.counts.groups == 0


def test_equivalent_normalized_locations_group_together() -> None:
    plan = gold.plan_dedup(
        [_row(1, location_text="San José, CA"), _row(2, location_text="san jose ca")]
    )
    assert plan.counts == gold.Counts(groups=1, postings=2)


def test_different_companies_never_group() -> None:
    plan = gold.plan_dedup([_row(1, company_id=1), _row(2, company_id=2)])
    assert plan.counts.groups == 0


def test_different_titles_never_group() -> None:
    plan = gold.plan_dedup([_row(1, title="Software Engineer"), _row(2, title="Data Analyst")])
    assert plan.counts.groups == 0


def test_singleton_is_not_a_group() -> None:
    plan = gold.plan_dedup([_row(1)])
    assert plan.groups == []
    assert plan.members == []


def test_rows_without_company_or_title_are_skipped() -> None:
    plan = gold.plan_dedup([_row(1, company_id=None), _row(2, title=""), _row(3, title="   ")])
    assert plan.counts.groups == 0


def test_group_ids_are_deterministic() -> None:
    rows = [_row(1), _row(2)]
    assert gold.plan_dedup(rows).groups[0]["id"] == gold.plan_dedup(rows).groups[0]["id"]


# Frequency cap ------------------------------------------------------------


def test_oversized_key_is_dropped_and_counted() -> None:
    rows = [_row(n) for n in range(gold._MAX_GROUP + 1)]
    plan = gold.plan_dedup(rows)
    assert plan.groups == []
    assert plan.counts.skipped_keys == 1
    assert plan.counts.skipped_postings == len(rows)


def test_key_at_the_cap_is_kept() -> None:
    plan = gold.plan_dedup([_row(n) for n in range(gold._MAX_GROUP)])
    assert plan.counts.groups == 1
    assert plan.counts.skipped_keys == 0


# Evidence -----------------------------------------------------------------


def test_stage_one_never_decides() -> None:
    plan = gold.plan_dedup([_row(1), _row(2)])
    group = plan.groups[0]
    assert group["state"] == "pending"
    assert group["evidence"]["keyer"] == "company_role_location"
    assert group["evidence"]["strategy_version"] == 2


def test_evidence_records_signals_it_does_not_key_on() -> None:
    plan = gold.plan_dedup(
        [
            _row(
                1,
                source_url="https://job-boards.greenhouse.io/acme/jobs/12345",
                location_text="San Jose, CA",
            ),
            _row(2, source="ashby:acme", location_text="San José, CA"),
        ]
    )
    members = plan.groups[0]["evidence"]["members"]
    assert members[0]["ats_identity"] == "greenhouse:acme/12345"
    assert members[1]["ats_identity"] is None
    assert members[1]["location"] == "san jose ca"


# URL identity -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse:acme/12345"),
        (
            "https://job-boards.greenhouse.io/acme/jobs/12345?gh_src=abc&gh_jid=9",
            "greenhouse:acme/12345",
        ),
        (
            "https://jobs.lever.co/acme/0c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c",
            "lever:acme/0c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c",
        ),
        (
            "https://jobs.ashbyhq.com/acme/0c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c/apply",
            "ashby:acme/0c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c",
        ),
        ("https://jobs.smartrecruiters.com/acme/744000012345678", "smartrecruiters:acme/744000012345678"),
        ("https://careers-acme.icims.com/jobs/4821/software-engineer", "icims:careers-acme/4821"),
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/External/job/San-Jose/Software-Engineer_R-12345",
            "workday:acme/r-12345",
        ),
    ],
)
def test_ats_identity_extracts_native_id(url: str, expected: str) -> None:
    assert gold.ats_identity(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://acme.com/careers",
        "https://careers.acme.com/search?q=engineer",
        "https://acme.wd1.myworkdayjobs.com/en-US/External",
    ],
)
def test_unparseable_urls_fail_closed(url: str | None) -> None:
    """A landing page shared by every posting must yield no identity at all."""
    assert gold.ats_identity(url) is None


def test_ats_identity_falls_back_across_urls() -> None:
    assert (
        gold.ats_identity("https://acme.com/careers", "https://jobs.lever.co/acme/" + "a" * 8 + "-2d3e-4f5a-8b7c-9d0e1f2a3b4c")
        is not None
    )


# Description hash ---------------------------------------------------------


def test_description_hash_ignores_case_and_whitespace() -> None:
    words = " ".join(f"word{n}" for n in range(30))
    assert gold.description_hash(words) == gold.description_hash(f"  {words.upper()}  ")


def test_short_descriptions_are_not_distinctive() -> None:
    assert gold.description_hash("Apply on our website.") is None
    assert gold.description_hash(None) is None


def test_description_hash_ignores_the_boilerplate_tail() -> None:
    body = " ".join(f"word{n}" for n in range(gold._DESCRIPTION_WORDS))
    assert gold.description_hash(body) == gold.description_hash(f"{body} equal opportunity employer")


# Skill enrichment ---------------------------------------------------------


def test_skills_payload_records_matches_hash_and_timestamp() -> None:
    description = "Experience with C++, Python, and SQL."

    payload = gold.skills_payload(description, parsed_at=NOW)

    assert payload == {
        "skills": ["cpp", "python", "sql"],
        "skills_meta": {
            "parsed_at": "2026-08-17T21:34:00Z",
            "input_hash": gold.skills_input_hash(description),
        },
    }


def test_empty_skill_result_is_stamped_as_processed() -> None:
    payload = gold.skills_payload(None, parsed_at=NOW)

    assert payload["skills"] == []
    assert payload["skills_meta"]["input_hash"] == gold.skills_input_hash("")


# Degree-field enrichment --------------------------------------------------


def test_degree_fields_payload_records_matches_hash_and_timestamp() -> None:
    description = "Bachelor's degree in Computer Science, Applied Mathematics, or BME."

    payload = gold.degree_fields_payload(description, parsed_at=NOW)

    assert payload == {
        "degree_fields": ["bioengineering", "computer_science", "mathematics"],
        "degree_fields_meta": {
            "version": 1,
            "parsed_at": "2026-08-17T21:34:00Z",
            "input_hash": gold.degree_fields_input_hash(description),
        },
    }


def test_empty_degree_result_is_stamped_as_processed() -> None:
    payload = gold.degree_fields_payload(None, parsed_at=NOW)

    assert payload["degree_fields"] == []
    assert payload["degree_fields_meta"]["input_hash"] == gold.degree_fields_input_hash("")


class _DegreesConnection:
    def __init__(self) -> None:
        self.rows = {
            1: {"id": 1, "description": "Degree in Computer Science.", "attributes": {}},
            2: {"id": 2, "description": None, "attributes": {}},
        }
        self.update_calls = 0

    async def fetch(self, query, last_id: int, limit: int):
        return [
            {"id": row["id"], "description": row["description"]}
            for row in self.rows.values()
            if row["id"] > last_id and "degree_fields_meta" not in row["attributes"]
        ][:limit]

    async def executemany(self, query, updates):
        self.update_calls += 1
        for posting_id, payload, description in updates:
            row = self.rows[posting_id]
            if (row["description"] or "") != description:
                continue
            if "degree_fields_meta" not in row["attributes"]:
                row["attributes"].update(payload)


@pytest.mark.asyncio
async def test_degrees_step_enriches_each_unstamped_posting_only_once() -> None:
    connection = _DegreesConnection()

    first = await gold._degrees_rebuild(connection, parsed_at=NOW)
    second = await gold._degrees_rebuild(connection, parsed_at=NOW)

    assert first == gold.Counts(postings=2)
    assert second == gold.Counts()
    assert connection.update_calls == 1
    assert connection.rows[1]["attributes"]["degree_fields"] == ["computer_science"]
    assert connection.rows[2]["attributes"]["degree_fields"] == []


class _SkillsConnection:
    def __init__(self) -> None:
        self.rows = {
            1: {"id": 1, "description": "Build Python and C++ services.", "attributes": {}},
            2: {"id": 2, "description": None, "attributes": {}},
        }
        self.update_calls = 0

    async def fetch(self, query: str, last_id: int, limit: int):
        return [
            {"id": row["id"], "description": row["description"]}
            for row in self.rows.values()
            if row["id"] > last_id and "skills_meta" not in row["attributes"]
        ][:limit]

    async def executemany(self, query: str, updates):
        self.update_calls += 1
        for posting_id, payload, description in updates:
            row = self.rows[posting_id]
            if (row["description"] or "") != description:
                continue
            if "skills_meta" not in row["attributes"]:
                row["attributes"].update(payload)


@pytest.mark.asyncio
async def test_skills_step_enriches_each_unstamped_posting_only_once() -> None:
    connection = _SkillsConnection()

    first = await gold._skills_rebuild(connection, parsed_at=NOW)
    second = await gold._skills_rebuild(connection, parsed_at=NOW)

    assert first == gold.Counts(postings=2)
    assert second == gold.Counts()
    assert connection.update_calls == 1
    assert connection.rows[1]["attributes"]["skills"] == ["cpp", "python"]
    assert connection.rows[2]["attributes"]["skills"] == []


@pytest.mark.asyncio
async def test_skills_step_reprocesses_a_posting_after_silver_invalidates_it() -> None:
    connection = _SkillsConnection()
    await gold._skills_rebuild(connection, parsed_at=NOW)
    connection.rows[1]["description"] = "Build SQL services."
    connection.rows[1]["attributes"].pop("skills")
    connection.rows[1]["attributes"].pop("skills_meta")

    counts = await gold._skills_rebuild(connection, parsed_at=NOW)

    assert counts == gold.Counts(postings=1)
    assert connection.rows[1]["attributes"]["skills"] == ["sql"]


# Step registry ------------------------------------------------------------


def test_steps_selects_all_or_one() -> None:
    # job_type is deliberately absent: it is a parse-time column derived per
    # vendor in the adapter, not a rebuildable gold artifact.
    assert [step.name for step in gold.steps()] == ["skills", "degrees", "dedup"]
    assert [step.name for step in gold.steps("degrees")] == ["degrees"]
    assert [step.name for step in gold.steps("skills")] == ["skills"]
    assert [step.name for step in gold.steps("dedup")] == ["dedup"]


def test_unknown_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown enrichment"):
        gold.steps("scoring")
