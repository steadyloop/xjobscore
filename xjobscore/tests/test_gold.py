"""Gold-layer enrichment and dedup behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from xjobscore import gold, skills


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
        "posted_at": None,
        "first_seen_at": None,
    }
    row.update(overrides)
    return row


# Keying -------------------------------------------------------------------


ASHBY_ID = "0c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c"


def _ashby_url(posting_id: str = ASHBY_ID, *, application: bool = False) -> str:
    suffix = "/application" if application else ""
    return f"https://jobs.ashbyhq.com/acme/{posting_id}{suffix}"


def test_same_ats_requisition_groups_despite_presentation_differences() -> None:
    plan = gold.plan_dedup([
        _row(
            1,
            title="Software Engineer",
            location_text="Seattle, Washington, United States",
            apply_url=_ashby_url(application=True),
        ),
        _row(
            2,
            source="hn:hn",
            title="Software Engineers",
            location_text="Seattle | ONSITE",
            apply_url=_ashby_url(),
        ),
    ])

    assert plan.counts == gold.Counts(groups=1, postings=2)
    assert plan.groups[0]["evidence"]["signal"] == "ats_requisition"


def test_different_ats_requisitions_never_fall_back_to_matching_role() -> None:
    other = "1c4c1b1a-2d3e-4f5a-8b7c-9d0e1f2a3b4c"
    plan = gold.plan_dedup([
        _row(1, apply_url=_ashby_url()),
        _row(2, apply_url=_ashby_url(other)),
    ])

    assert plan.counts.groups == 0


def test_same_ats_requisition_never_crosses_company_identity() -> None:
    plan = gold.plan_dedup([
        _row(1, company_id=1, apply_url=_ashby_url()),
        _row(2, company_id=2, apply_url=_ashby_url(application=True)),
    ])

    assert plan.counts.groups == 0


def test_source_url_supplies_requisition_when_apply_url_is_missing() -> None:
    plan = gold.plan_dedup([
        _row(1, source_url=_ashby_url()),
        _row(2, source_url=_ashby_url(application=True)),
    ])

    assert plan.counts == gold.Counts(groups=1, postings=2)
    assert plan.groups[0]["evidence"]["signal"] == "ats_requisition"


def test_exact_unparseable_apply_url_is_the_second_choice() -> None:
    url = "https://careers.example.test/postings/42/apply?ref=board"
    plan = gold.plan_dedup([
        _row(1, title="Full title", location_text="New York", apply_url=url),
        _row(2, title="Short title", location_text="Remote", apply_url=url),
    ])

    assert plan.counts == gold.Counts(groups=1, postings=2)
    assert plan.groups[0]["evidence"]["signal"] == "exact_apply_url"


def test_different_unparseable_apply_urls_never_use_role_fallback() -> None:
    plan = gold.plan_dedup([
        _row(1, apply_url="https://careers.example.test/postings/41"),
        _row(2, apply_url="https://careers.example.test/postings/42"),
    ])

    assert plan.counts.groups == 0


def test_same_company_title_and_missing_location_group_together() -> None:
    plan = gold.plan_dedup(
        [_row(1, title="Software Engineer"), _row(2, title="software   engineer!")]
    )
    assert plan.counts == gold.Counts(groups=1, postings=2)
    assert {member["group_id"] for member in plan.members} == {plan.groups[0]["id"]}
    assert plan.groups[0]["evidence"]["signal"] == "company_role_location"


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


def test_rows_without_company_or_without_any_identity_are_skipped() -> None:
    plan = gold.plan_dedup([_row(1, company_id=None), _row(2, title=""), _row(3, title="   ")])
    assert plan.counts.groups == 0


def test_requisition_identity_does_not_require_a_title() -> None:
    plan = gold.plan_dedup([
        _row(1, title="", apply_url=_ashby_url()),
        _row(2, title="", apply_url=_ashby_url(application=True)),
    ])

    assert plan.counts == gold.Counts(groups=1, postings=2)


def test_group_ids_are_deterministic() -> None:
    rows = [_row(1), _row(2)]
    assert gold.plan_dedup(rows).groups[0]["id"] == gold.plan_dedup(rows).groups[0]["id"]


# Primary selection --------------------------------------------------------


def test_direct_ats_posting_is_primary_over_newer_aggregator() -> None:
    plan = gold.plan_dedup([
        _row(
            1,
            source="ashby:acme",
            apply_url=_ashby_url(),
            posted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
        _row(
            2,
            source="hn:hn",
            apply_url=_ashby_url(application=True),
            posted_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        ),
    ])

    assert plan.groups[0]["primary_posting_id"] == 1


def test_newest_posted_at_is_primary_within_same_source_quality() -> None:
    plan = gold.plan_dedup([
        _row(1, posted_at=datetime(2026, 8, 1, tzinfo=timezone.utc)),
        _row(2, posted_at=datetime(2026, 8, 20, tzinfo=timezone.utc)),
    ])

    assert plan.groups[0]["primary_posting_id"] == 2


def test_newest_first_seen_breaks_posted_at_tie() -> None:
    posted = datetime(2026, 8, 1, tzinfo=timezone.utc)
    plan = gold.plan_dedup([
        _row(1, posted_at=posted, first_seen_at=datetime(2026, 8, 2, tzinfo=timezone.utc)),
        _row(2, posted_at=posted, first_seen_at=datetime(2026, 8, 3, tzinfo=timezone.utc)),
    ])

    assert plan.groups[0]["primary_posting_id"] == 2


def test_exact_primary_tie_stays_unselected_without_id_tiebreaker() -> None:
    plan = gold.plan_dedup([_row(7), _row(99)])

    assert plan.groups[0]["primary_posting_id"] is None


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


def test_authoritative_requisition_identity_is_not_frequency_capped() -> None:
    rows = [_row(n, apply_url=_ashby_url()) for n in range(gold._MAX_GROUP + 1)]

    plan = gold.plan_dedup(rows)

    assert plan.counts == gold.Counts(groups=1, postings=len(rows))
    assert plan.counts.skipped_keys == 0


# Evidence -----------------------------------------------------------------


def test_stage_one_never_decides() -> None:
    plan = gold.plan_dedup([_row(1), _row(2)])
    group = plan.groups[0]
    assert group["state"] == "pending"
    assert group["evidence"]["keyer"] == "ats_requisition_then_apply_then_unlinked_role"
    assert group["evidence"]["strategy_version"] == 4
    assert group["evidence"]["primary_selector"] == "direct_ats_then_posted_then_first_seen"


def test_evidence_records_signals_it_does_not_key_on() -> None:
    url = _ashby_url()
    plan = gold.plan_dedup(
        [
            _row(
                1,
                apply_url=url,
                location_text="San Jose, CA",
            ),
            _row(
                2,
                source="hn:hn",
                apply_url=f"{url}/application",
                location_text="San José, CA",
            ),
        ]
    )
    members = plan.groups[0]["evidence"]["members"]
    assert members[0]["ats_identity"] == f"ashby:acme/{ASHBY_ID}"
    assert members[1]["ats_identity"] == f"ashby:acme/{ASHBY_ID}"
    assert members[1]["location"] == "san jose ca"


@pytest.mark.asyncio
async def test_rebuild_fetches_descriptions_only_for_group_members(monkeypatch) -> None:
    candidates = [
        {key: value for key, value in _row(1).items() if key != "description"},
        {key: value for key, value in _row(2).items() if key != "description"},
        {
            key: value
            for key, value in _row(3, title="Unique title").items()
            if key != "description"
        },
    ]
    requested: list[int] = []

    async def fake_candidates(_conn):
        return candidates

    async def fake_descriptions(_conn, posting_ids):
        requested.extend(posting_ids)
        return [
            {"id": posting_id, "description": "word " * 30}
            for posting_id in posting_ids
        ]

    monkeypatch.setattr(gold.store, "dedup_candidates", fake_candidates)
    monkeypatch.setattr(gold.store, "dedup_descriptions", fake_descriptions)

    counts = await gold._dedup_rebuild(object(), dry_run=True)

    assert counts == gold.Counts(groups=1, postings=2)
    assert requested == [1, 2]


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
            "version": 2,
            "taxonomy_hash": skills.taxonomy_hash(),
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

    payload = gold.degree_fields_payload("Engineer", description, parsed_at=NOW)

    assert payload == {
        "degree_fields": ["computer_science", "mathematics", "med_device"],
        "degree_fields_meta": {
            "version": 3,
            "parsed_at": "2026-08-17T21:34:00Z",
            "input_hash": gold.degree_fields_input_hash("Engineer", description),
        },
    }


def test_empty_degree_result_is_stamped_as_processed() -> None:
    payload = gold.degree_fields_payload("Engineer", None, parsed_at=NOW)

    assert payload["degree_fields"] == []
    assert payload["degree_fields_meta"]["input_hash"] == gold.degree_fields_input_hash(
        "Engineer", None
    )


def test_degree_fields_include_title_when_description_has_no_signal() -> None:
    payload = gold.degree_fields_payload(
        "Biomedical Engineering Intern", "Join our summer program.", parsed_at=NOW
    )

    assert payload["degree_fields"] == ["med_device"]


class _DegreesConnection:
    def __init__(self) -> None:
        self.rows = {
            1: {
                "id": 1,
                "title": "Engineer",
                "description": "Degree in Computer Science.",
                "attributes": {},
            },
            2: {"id": 2, "title": "Engineer", "description": None, "attributes": {}},
        }
        self.update_calls = 0

    async def fetch(self, query, last_id: int, limit: int, version: str):
        return [
            {"id": row["id"], "title": row["title"], "description": row["description"]}
            for row in self.rows.values()
            if row["id"] > last_id
            and str(row["attributes"].get("degree_fields_meta", {}).get("version", "")) != version
        ][:limit]

    async def executemany(self, query, updates):
        self.update_calls += 1
        for posting_id, payload, title, description, version in updates:
            row = self.rows[posting_id]
            if row["title"] != title or (row["description"] or "") != description:
                continue
            if str(row["attributes"].get("degree_fields_meta", {}).get("version", "")) != version:
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


@pytest.mark.asyncio
async def test_degrees_step_rebuilds_postings_from_an_older_taxonomy() -> None:
    connection = _DegreesConnection()
    connection.rows[1]["attributes"] = {
        "degree_fields": ["bioengineering"],
        "degree_fields_meta": {"version": 1},
    }

    result = await gold._degrees_rebuild(connection, parsed_at=NOW)

    assert result == gold.Counts(postings=2)
    assert connection.rows[1]["attributes"]["degree_fields"] == ["computer_science"]
    assert connection.rows[1]["attributes"]["degree_fields_meta"]["version"] == 3


class _SkillsConnection:
    def __init__(self) -> None:
        self.rows = {
            1: {"id": 1, "description": "Build Python and C++ services.", "attributes": {}},
            2: {"id": 2, "description": None, "attributes": {}},
        }
        self.update_calls = 0

    async def fetch(
        self, query: str, last_id: int, limit: int, version: str, taxonomy_hash: str
    ):
        return [
            {"id": row["id"], "description": row["description"]}
            for row in self.rows.values()
            if row["id"] > last_id
            and (
                str(row["attributes"].get("skills_meta", {}).get("version", ""))
                != version
                or row["attributes"].get("skills_meta", {}).get("taxonomy_hash", "")
                != taxonomy_hash
            )
        ][:limit]

    async def executemany(self, query: str, updates):
        self.update_calls += 1
        for posting_id, payload, description, version, taxonomy_hash in updates:
            row = self.rows[posting_id]
            if (row["description"] or "") != description:
                continue
            metadata = row["attributes"].get("skills_meta", {})
            if (
                str(metadata.get("version", "")) != version
                or metadata.get("taxonomy_hash", "") != taxonomy_hash
            ):
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


# Role summaries -----------------------------------------------------------


class _SummariesConnection:
    def __init__(self) -> None:
        self.rows = {
            1: {
                "id": 1,
                "title": "Software Engineering Intern",
                "description": (
                    "Responsibilities\n"
                    "Build Python services that process customer events at scale.\n"
                    "Own a backend project from design through production rollout.\n"
                    "Collaborate with product and infrastructure engineers to improve reliability.\n"
                    "Qualifications\n"
                    "Currently enrolled in a computer science degree program.\n"
                    "Experience with Python, SQL, and distributed systems is preferred."
                ),
                "job_type": "intern",
                "attributes": {},
            },
            2: {
                "id": 2,
                "title": "Research Student",
                "description": None,
                "job_type": "research",
                "attributes": {},
            },
            3: {
                "id": 3,
                "title": "Senior Manager",
                "description": "Lead an established team and manage its annual budget.",
                "job_type": None,
                "attributes": {},
            },
        }
        self.update_calls = 0

    async def fetch(self, query: str, last_id: int, limit: int, version: str):
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
            }
            for row in self.rows.values()
            if row["id"] > last_id
            and row["job_type"] is not None
            and str(row["attributes"].get("summary_meta", {}).get("version", ""))
            != version
        ][:limit]

    async def executemany(self, query: str, updates):
        self.update_calls += 1
        for posting_id, payload, title, description, version in updates:
            row = self.rows[posting_id]
            if row["title"] != title or (row["description"] or "") != description:
                continue
            if str(row["attributes"].get("summary_meta", {}).get("version", "")) != version:
                row["attributes"].update(payload)


@pytest.mark.asyncio
async def test_summaries_step_stamps_results_and_safe_abstentions_once() -> None:
    connection = _SummariesConnection()

    first = await gold._summaries_rebuild(connection, parsed_at=NOW)
    second = await gold._summaries_rebuild(connection, parsed_at=NOW)

    assert first == gold.Counts(postings=2)
    assert second == gold.Counts()
    assert connection.update_calls == 1
    summary = connection.rows[1]["attributes"]["summary"]
    assert 40 <= len(summary.split()) <= 50
    assert connection.rows[1]["attributes"]["summary_meta"] == {
        "version": 1,
        "generator": "extractive",
        "status": "ok",
        "parsed_at": "2026-08-17T21:34:00Z",
        "input_hash": gold.summary_input_hash(
            connection.rows[1]["title"], connection.rows[1]["description"]
        ),
    }
    assert connection.rows[2]["attributes"]["summary"] is None
    assert connection.rows[2]["attributes"]["summary_meta"]["status"] == "insufficient_evidence"
    assert connection.rows[3]["attributes"] == {}


@pytest.mark.asyncio
async def test_skills_step_rebuilds_postings_from_an_older_taxonomy() -> None:
    connection = _SkillsConnection()
    connection.rows[1]["attributes"] = {
        "skills": ["legacy-skill"],
        "skills_meta": {"version": 1, "taxonomy_hash": "old"},
    }

    counts = await gold._skills_rebuild(connection, parsed_at=NOW)

    assert counts == gold.Counts(postings=2)
    assert connection.rows[1]["attributes"]["skills"] == ["cpp", "python"]
    assert connection.rows[1]["attributes"]["skills_meta"]["version"] == 2
    assert (
        connection.rows[1]["attributes"]["skills_meta"]["taxonomy_hash"]
        == skills.taxonomy_hash()
    )


# Step registry ------------------------------------------------------------


def test_steps_selects_all_or_one() -> None:
    # job_type is deliberately absent: it is a parse-time column derived per
    # vendor in the adapter, not a rebuildable gold artifact.
    assert [step.name for step in gold.steps()] == [
        "skills", "degrees", "summaries", "dedup"
    ]
    assert [step.name for step in gold.steps("degrees")] == ["degrees"]
    assert [step.name for step in gold.steps("skills")] == ["skills"]
    assert [step.name for step in gold.steps("summaries")] == ["summaries"]
    assert [step.name for step in gold.steps("dedup")] == ["dedup"]


def test_unknown_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown enrichment"):
        gold.steps("scoring")
