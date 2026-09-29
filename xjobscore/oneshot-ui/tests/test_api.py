import asyncio
from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from oneshot_ui.app import create_app
from oneshot_ui.auth import session_account
from oneshot_ui.config import Settings
from oneshot_ui.repository import (
    PostgresRepository,
    UNCLASSIFIED,
    _ilike_pattern,
    _location_label,
    _skill_search_ids,
)
from oneshot_ui.location_policy import (
    US_HUB_KEYS,
    US_NORCAL_KEYS,
    US_SEATTLE_KEYS,
    US_SOCAL_KEYS,
    US_SUBURB_KEYS,
)

TEST_ACCOUNT_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def signed_in_app(**kwargs):
    """An app whose routes see a signed-in account.

    Overriding the dependency rather than forging a session cookie keeps these
    tests about the API contract: the sign-in flow itself is covered separately
    in test_auth.py.
    """
    application = create_app(Settings(dsn="injected"), **kwargs)
    application.dependency_overrides[session_account] = lambda: TEST_ACCOUNT_ID
    return application


class FakeRepository:
    def __init__(self) -> None:
        self.connected = False
        # One PNG and one company that the harvest never resolved.
        self.logos = {7: b"\x89PNG\r\n\x1a\n fake", 8: None}
        self.jobs = [
            {
                "id": 41,
                "source": "greenhouse:example-labs",
                "source_url": "https://jobs.example.test/41",
                "company": "Example Labs",
                "title": "Summer Software Engineering Intern",
                "summary": "Build Python services and ship a scoped production project with the platform team.",
                "locations": [{"country": "US", "state": None, "city": None}],
                "location": "United States",
                "apply_url": "https://jobs.example.test/41/apply",
                "job_type": "intern",
                "job_domain": "software",
                "remote": "yes",
                "attributes": {
                    "version": 1,
                    "term": "Summer 2027",
                    "skills": ["python", "postgresql"],
                    "degree": ["cs"],
                },
                "skills": [
                    {
                        "id": "python",
                        "name": "Python",
                        "label": "Python",
                        "category": "language",
                    },
                    {
                        "id": "postgresql",
                        "name": "PostgreSQL",
                        "label": "PostgreSQL",
                        "category": "datastore",
                    },
                ],
                "other_sources": [],
            }
        ]
        self.applications: list[dict] = []
        self.feedback: dict[int, dict] = {}
        self.next_id = 1

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    def for_account(self, account_id):
        # Account scoping lives in SQL, which this fake does not have. The real
        # isolation guarantee is asserted against PostgreSQL in
        # test_postgres_repository.py.
        return self

    async def list_jobs(self, **options):
        application_job_ids = {row["job_id"] for row in self.applications if row["job_id"]}
        hidden = application_job_ids | set(self.feedback)
        rows = [row for row in self.jobs if row["id"] not in hidden]
        degree_fields = set(options["degree_fields"])
        domains = degree_fields.intersection({"software", "finance", "unlabeled"})
        if domains:
            rows = [row for row in rows if row.get("job_domain") in domains]
            degree_fields -= domains
        if degree_fields:
            rows = [
                row
                for row in rows
                if degree_fields.intersection(row["attributes"].get("degree", []))
            ]
        job_type_counts = {
            job_type: sum(row.get("job_type") == job_type for row in rows)
            for job_type in ("intern", "coop", "student", "new_grad", "research")
        }
        types = set(options["job_types"])
        if types:
            rows = [
                row
                for row in rows
                if row.get("job_type") in types
                or (row.get("job_type") is None and UNCLASSIFIED in types)
            ]
        return {"jobs": deepcopy(rows), "total": len(rows), "job_type_counts": job_type_counts, "limit": options["limit"], "offset": options["offset"]}

    async def company_logo(self, company_id):
        return self.logos.get(company_id)

    async def source_coverage(self):
        return {
            "sources": [{"source": "ashby", "type": "ATS", "board_count": 1, "coverage": 1, "last_poll_at": None, "health": "Not polled", "added_today": 1}],
            "source_count": 1,
            "total_coverage": 1,
            "total_added_today": 1,
        }

    async def board_map(self):
        return {
            "providers": [
                {
                    "source": "ashby",
                    "board_count": 2,
                    "err_count": 1,
                    "boards": [
                        {"key": "acme", "company": "Acme Corp", "state": "ok", "strikes": 0, "last_poll_at": None, "coverage": 3},
                        {"key": "initech", "company": "Initech", "state": "degraded", "strikes": 1, "last_poll_at": None, "coverage": 0},
                    ],
                }
            ],
            "totals": {"boards": 2, "providers": 1, "ok": 1, "erroring": 1, "stale": 0, "paused": 0, "never": 0, "added_today": 3},
        }

    def _new_application(self, *, job_id, company, title, url, location, note, external):
        application = {
            "id": self.next_id,
            "job_id": job_id,
            "external": external,
            "company": company,
            "title": title,
            "location": location,
            "stage": "considering",
            "closed_reason": None,
            "apply_url_snapshot": url,
            "resume_variant": None,
            "submitted_at": None,
            "next_action": None,
            "next_due_at": None,
            "note": note,
            "events": [],
        }
        self.next_id += 1
        self.applications.append(application)
        return deepcopy(application)

    async def create_application(self, job_id):
        job = next((row for row in self.jobs if row["id"] == job_id), None)
        if job is None:
            return None
        existing = next((row for row in self.applications if row["job_id"] == job_id), None)
        if existing:
            result = deepcopy(existing)
            result["already_exists"] = True
            return result
        return self._new_application(job_id=job_id, company=job["company"], title=job["title"], url=job["apply_url"], location=job["location"], note="", external=False)

    async def create_external_application(self, **values):
        return self._new_application(job_id=None, company=values["company"], title=values["title"], url=values["url"], location=values["location"], note=values["note"], external=True)

    async def list_applications(self):
        return {
            "applications": [
                {key: deepcopy(value) for key, value in row.items() if key != "events"}
                for row in self.applications
            ]
        }

    async def get_application(self, application_id):
        return next((deepcopy(row) for row in self.applications if row["id"] == application_id), None)

    async def update_application(self, application_id, changes):
        row = next((row for row in self.applications if row["id"] == application_id), None)
        if row is None:
            return None
        if not row["external"] and any(key in changes for key in ("company", "title", "location", "apply_url_snapshot")):
            raise ValueError("discovered application snapshots cannot be edited")
        for key in ("company", "title", "location"):
            if key in changes:
                row[key] = changes[key]
        for key in ("stage", "closed_reason", "apply_url_snapshot", "resume_variant", "submitted_at", "next_action", "next_due_at", "note"):
            if key in changes:
                row[key] = changes[key]
        return deepcopy(row)

    async def delete_application(self, application_id):
        before = len(self.applications)
        self.applications = [row for row in self.applications if row["id"] != application_id]
        return len(self.applications) != before

    async def set_feedback(self, job_id, *, kind, skip_reason=None, corrections=None):
        if not any(row["id"] == job_id for row in self.jobs):
            return None
        row = {
            "job_id": job_id,
            "kind": kind,
            "skip_reason": skip_reason,
            "corrections": deepcopy(corrections or []),
        }
        self.feedback[job_id] = row
        return deepcopy(row)

    async def delete_feedback(self, job_id):
        return self.feedback.pop(job_id, None) is not None

    async def list_feedback(self, *, limit, offset):
        jobs = {row["id"]: row for row in self.jobs}
        hidden = [
            {**deepcopy(row), "job": deepcopy(jobs[job_id])}
            for job_id, row in reversed(self.feedback.items())
        ]
        return {"total": len(hidden), "hidden": hidden[offset:offset + limit]}


def test_job_response_exposes_provenance_without_internal_trace_fields() -> None:
    result = PostgresRepository._job({
        "id": 41,
        "source": "greenhouse:example-labs",
        "source_url": "https://jobs.example.test/41",
        "company_name": "Example Labs",
        "title": "Summer Software Engineering Intern",
        "location_text": "This raw value must not reach the UI",
        "locations": '[{"country":"US","state":"CA","city":"San Francisco"}]',
        "apply_url": "https://jobs.example.test/41/apply",
        "posted_at": None,
        "first_seen_at": None,
        "last_seen_at": None,
        "status": "active",
        "attributes": {
            "version": 1,
            "summary": "Build Python services and ship a scoped production project with the platform team.",
        },
        "content_hash": "internal-hash",
        "raw_id": 99,
        "source_key": "internal-key",
        "other_sources": [
            {
                "source": "workday:example-labs",
                "url": "https://workday.example.test/41",
                "posted_at": "2026-08-20T00:00:00+00:00",
                "first_seen_at": "2026-08-20T01:00:00+00:00",
                "source_key": "must-not-leak",
            },
            {"source": "", "url": "https://invalid.example.test"},
        ],
    })

    assert result["source"] == "greenhouse:example-labs"
    assert result["source_url"] == "https://jobs.example.test/41"
    assert result["locations"] == [
        {"country": "US", "state": "CA", "city": "San Francisco"}
    ]
    assert result["location"] == "San Francisco, CA"
    assert result["skills"] == []
    assert result["summary"].startswith("Build Python services")
    assert result["other_sources"] == [
        {
            "source": "workday:example-labs",
            "url": "https://workday.example.test/41",
            "posted_at": "2026-08-20T00:00:00+00:00",
            "first_seen_at": "2026-08-20T01:00:00+00:00",
        }
    ]
    assert not ({"description", "content_hash", "raw_id", "source_key"} & result.keys())


def test_job_response_derives_structured_skills_without_mutating_attributes() -> None:
    stored = [
        "go",
        "Kubernetes",
        "artificial-intelligence",
        "legacy-tool",
        "go",
        "",
        7,
    ]
    result = PostgresRepository._job({
        "id": 42,
        "title": "Backend Engineer",
        "attributes": {"version": 1, "skills": stored},
    })

    assert result["attributes"]["skills"] == stored
    assert result["skills"] == [
        {"id": "go", "name": "Go", "label": "Go", "category": "language"},
        {
            "id": "kubernetes",
            "name": "Kubernetes",
            "label": "Kubernetes",
            "category": "platform",
        },
        {
            "id": "artificial-intelligence",
            "name": "Artificial intelligence (AI)",
            "label": "AI",
            "category": "concept",
        },
        {
            "id": "legacy-tool",
            "name": "legacy-tool",
            "label": "legacy-tool",
            "category": None,
        },
    ]


def test_location_label_is_compact_and_uses_only_normalized_locations() -> None:
    assert _location_label([
        {"country": "US", "state": "NY", "city": "New York City"},
        {"country": "GB", "state": "ENG", "city": "London"},
        {"country": "SG", "state": None, "city": "Singapore"},
        {"country": "DE", "state": "16", "city": "Berlin"},
    ]) == "New York City, NY · London, United Kingdom · Singapore · +1 more"
    assert _location_label([]) is None


def test_us_location_policy_keys_rank_hubs_suburbs_and_regions() -> None:
    # These are the lists list_jobs binds into its ORDER BY, so the policy is
    # asserted on the data the SQL actually reads.
    assert "CA|San Francisco" in US_HUB_KEYS
    assert "CA|Palo Alto" in US_SUBURB_KEYS
    assert "CA|Palo Alto" not in US_HUB_KEYS
    assert not {"MN|Rochester"} & {*US_HUB_KEYS, *US_SUBURB_KEYS}
    assert "CA|San Francisco" in US_NORCAL_KEYS
    assert "CA|Los Angeles" in US_SOCAL_KEYS
    assert "WA|Seattle" in US_SEATTLE_KEYS
    assert not {"NY|New York City"} & {
        *US_NORCAL_KEYS, *US_SOCAL_KEYS, *US_SEATTLE_KEYS
    }


@pytest.mark.asyncio
async def test_search_matches_the_ats_vendor_half_of_source() -> None:
    """Typing a vendor name lists that vendor's boards, keyed on the prefix only."""

    class QueryPool:
        def __init__(self):
            self.queries = []

        async def fetchval(self, query, *args):
            return 0

        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            return {"intern": 0, "coop": 0, "student": 0, "new_grad": 0, "research": 0}

        async def fetch(self, query, *args):
            self.queries.append((query, args))
            return []

    pool = QueryPool()
    repository = PostgresRepository("injected", pool=pool)
    await repository.list_jobs(job_types=["intern"], q="greenhouse")

    assert pool.queries
    for query, args in pool.queries:
        assert "split_part(j.source, ':', 1) ILIKE" in query
        assert "%greenhouse%" in args


def test_skill_keyword_search_resolves_ids_without_description_search() -> None:
    assert "cpp" in _skill_search_ids("C++")
    assert "kubernetes" in _skill_search_ids("Kubernetes")
    assert _skill_search_ids("not-a-real-skill") == ()


@pytest.mark.asyncio
async def test_job_count_does_not_block_the_visible_page() -> None:
    """The count and page reads must occupy independent pool connections.

    The count deliberately waits until the page query starts. A sequential
    implementation deadlocks and hits the timeout; the concurrent repository
    returns immediately with the existing API shape.
    """

    class GatedPool:
        def __init__(self) -> None:
            self.page_started = asyncio.Event()
            self.count_args = ()
            self.count_query = ""
            self.page_args = ()
            self.page_query = ""

        async def fetchval(self, query, *args):
            raise AssertionError("standard job types derive total from tab counts")

        async def fetchrow(self, query, *args):
            self.count_query = query
            self.count_args = args
            await self.page_started.wait()
            return {"intern": 1, "coop": 0, "student": 0, "new_grad": 0, "research": 0}

        async def fetch(self, query, *args):
            self.page_query = query
            self.page_args = args
            self.page_started.set()
            return [
                {
                    "id": 41,
                    "source": "greenhouse:example-labs",
                    "source_url": "https://jobs.example.test/41",
                    "company_name": "Example Labs",
                    "title": "Summer Software Engineering Intern",
                    "locations": [{"country": "US", "state": None, "city": None}],
                    "apply_url": "https://jobs.example.test/41/apply",
                    "posted_at": None,
                    "first_seen_at": None,
                    "last_seen_at": None,
                    "status": "active",
                    "attributes": {
                        "version": 1,
                        "job_type": "intern",
                        "summary": "Build Python services with the platform team.",
                    },
                }
            ]

    pool = GatedPool()
    repository = PostgresRepository("injected", pool=pool)

    result = await asyncio.wait_for(
        repository.list_jobs(
            job_types=["intern"],
            degree_fields=["cs"],
            exclude_job_ids=[99],
            q="Kubernetes",
            limit=25,
            offset=50,
        ),
        timeout=1,
    )

    assert result["total"] == 1
    assert result["job_type_counts"]["intern"] == 1
    assert result["jobs"][0]["id"] == 41
    assert result["jobs"][0]["location"] == "United States"
    # COUNT sees only account + filters; LIMIT/OFFSET belong only to the page.
    # An ordinary focus area binds nothing: `degree` is accepted but no
    # longer gates the query, so "cs" above contributes no parameter here.
    assert len(pool.count_args) == 4
    assert ["cs"] not in pool.count_args
    assert pool.count_args[1] == [99]
    assert pool.count_args[2] == "%Kubernetes%"
    assert "kubernetes" in pool.count_args[3]
    assert pool.page_args[-2:] == (25, 50)
    array_args = [value for value in pool.page_args if isinstance(value, list)]
    assert any("CA|San Francisco" in value and "NY|New York City" in value for value in array_args)
    assert any("CA|Palo Alto" in value for value in array_args)
    assert any("CA|San Francisco" in value and "CA|Palo Alto" in value for value in array_args)
    assert any("CA|Los Angeles" in value and "CA|San Diego" in value for value in array_args)
    assert any("WA|Seattle" in value and "WA|Redmond" in value for value in array_args)
    where = pool.page_query.split("WHERE", 1)[1]
    assert "j.description" not in pool.page_query
    assert "(j.attributes -> 'skills') ?|" in where
    assert "j.locations = '[]'::jsonb" in where
    assert "AT TIME ZONE 'America/Los_Angeles'" in pool.page_query
    assert "SELECT 1 FROM jsonb_array_elements(j.locations) location" in pool.page_query
    assert "THEN 1" in pool.page_query and "ELSE 4" in pool.page_query
    for query in (pool.count_query, pool.page_query):
        assert "LEFT JOIN job_posting_dup_group g" in query
        assert "g.primary_posting_id IS NOT NULL" in query
        assert "g.state <> 'rejected'" in query
        assert "stored_primary.status = 'active'" in query
        assert "active_member.status = 'active'" in query
    assert "tracked.dup_group_id = j.dup_group_id" in pool.page_query
    assert "excluded.dup_group_id = j.dup_group_id" in pool.page_query
    assert "AS other_sources" in pool.page_query
    assert "sibling.status = 'active'" in pool.page_query


@pytest.mark.asyncio
async def test_oldest_sort_orders_posting_dates_and_ids_ascending() -> None:
    class RecordingPool:
        def __init__(self) -> None:
            self.page_query = ""

        async def fetchval(self, query, *args):
            return 0

        async def fetchrow(self, query, *args):
            return {
                "intern": 0,
                "coop": 0,
                "student": 0,
                "new_grad": 0,
                "research": 0,
            }

        async def fetch(self, query, *args):
            self.page_query = query
            return []

    pool = RecordingPool()
    repository = PostgresRepository("injected", pool=pool)
    await repository.list_jobs(job_types=["intern"], sort="oldest")

    assert "::date ASC NULLS LAST" in pool.page_query
    assert "COALESCE(j.posted_at, j.first_seen_at) ASC NULLS LAST" in pool.page_query
    assert "j.id ASC" in pool.page_query


@pytest.mark.asyncio
async def test_season_filter_searches_title_and_description_and_skips_count_cache() -> None:
    class RecordingPool:
        def __init__(self) -> None:
            self.queries = []

        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            return {
                "intern": 0,
                "coop": 0,
                "student": 0,
                "new_grad": 0,
                "research": 0,
            }

        async def fetch(self, query, *args):
            self.queries.append((query, args))
            return []

    pool = RecordingPool()
    repository = PostgresRepository("injected", pool=pool)

    await repository.list_jobs(job_types=["intern"], season="Fall")
    await repository.list_jobs(job_types=["intern"], season="Fall")

    assert len(pool.queries) == 4
    for query, args in pool.queries:
        assert "j.title ~*" in query
        assert "COALESCE(j.description, '') ~*" in query
        assert r"\mfall\M" in args
    with pytest.raises(ValueError, match="unknown season"):
        await repository.list_jobs(job_types=["intern"], season="spring")


@pytest.mark.asyncio
async def test_bio_degree_filter_uses_title_and_employer_evidence() -> None:
    class RecordingPool:
        def __init__(self) -> None:
            self.queries = []

        async def fetchval(self, query, *args):
            self.queries.append((query, args))
            return 0

        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            return {
                "intern": 0,
                "coop": 0,
                "student": 0,
                "new_grad": 0,
                "research": 0,
            }

        async def fetch(self, query, *args):
            self.queries.append((query, args))
            return []

    pool = RecordingPool()
    repository = PostgresRepository("injected", pool=pool)
    await repository.list_jobs(
        job_types=["intern"],
        degree_fields=["biotech"],
    )

    assert len(pool.queries) == 3
    employer_query, employer_args = pool.queries[0]
    assert "WITH company_postings AS MATERIALIZED" in employer_query
    assert "c.tags ? 'biotech'" in employer_query
    assert "COALESCE(ct.direct_count, 0) >= 5" in employer_query
    assert ">= 0.02" in employer_query
    assert "COALESCE(ct.direct_count, 0) >= 3" in employer_query
    assert any("bio[- ]?engineer" in str(value) for value in employer_args)

    for query, args in pool.queries[1:]:
        assert "company_postings AS MATERIALIZED" not in query
        assert "c.id = ANY" in query
        assert "COALESCE(j.description" not in query
        assert any("bio[- ]?engineer" in str(value) for value in args)

    # Account-scoped repository clones share the same five-minute employer
    # snapshot rather than recomputing it for each signed-in request.
    await repository.for_account(TEST_ACCOUNT_ID).list_jobs(
        job_types=["intern"],
        degree_fields=["biotech"],
    )
    assert len(pool.queries) == 5


def test_live_api_contract_uses_repository_and_postgres_vocabulary() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        assert repository.connected
        assert client.get("/api/health").json() == {"ok": True}
        jobs = client.get(
            "/api/jobs",
            params={"job_type": "intern", "degree_field": "cs"},
        ).json()
        assert jobs["total"] == 1
        assert jobs["job_type_counts"] == {
            "intern": 1, "coop": 0, "student": 0, "new_grad": 0, "research": 0,
        }
        assert jobs["jobs"][0]["attributes"]["skills"] == ["python", "postgresql"]
        assert jobs["jobs"][0]["attributes"]["degree"] == ["cs"]
        assert jobs["jobs"][0]["skills"] == [
            {
                "id": "python",
                "name": "Python",
                "label": "Python",
                "category": "language",
            },
            {
                "id": "postgresql",
                "name": "PostgreSQL",
                "label": "PostgreSQL",
                "category": "datastore",
            },
        ]
        created = client.post("/api/jobs/41/applications").json()
        assert created["stage"] == "considering"
        assert client.post("/api/jobs/41/applications").json()["already_exists"]

        external = client.post(
            "/api/applications/external",
            json={"company": "Manual Co", "title": "Engineer", "url": "https://manual.example/job"},
        ).json()
        assert external["external"] is True
        updated = client.patch(
            f"/api/applications/{external['id']}",
            json={"stage": "closed", "closed_reason": "withdrawn"},
        ).json()
        assert updated["stage"] == "closed"
        assert updated["closed_reason"] == "withdrawn"
        assert client.delete(f"/api/applications/{external['id']}").status_code == 204
        assert client.get("/api/source-coverage").json()["total_coverage"] == 1
        board_map = client.get("/api/source-coverage/boards").json()
        assert board_map["totals"] == {"boards": 2, "providers": 1, "ok": 1, "erroring": 1, "stale": 0, "paused": 0, "never": 0, "added_today": 3}
        assert board_map["providers"][0]["err_count"] == 1
        assert [board["state"] for board in board_map["providers"][0]["boards"]] == ["ok", "degraded"]
    assert not repository.connected


def test_marking_an_application_applied_takes_no_extra_confirmation() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        created = client.post("/api/jobs/41/applications").json()
        response = client.patch(
            f"/api/applications/{created['id']}", json={"stage": "applied"}
        )
        assert response.status_code == 200
        assert response.json()["stage"] == "applied"


def test_location_is_editable_only_on_external_applications() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        external = client.post(
            "/api/applications/external",
            json={
                "company": "Manual Co",
                "title": "Engineer",
                "url": "https://manual.example/job",
                "location": "Boston, MA",
            },
        ).json()
        moved = client.patch(
            f"/api/applications/{external['id']}", json={"location": "Remote (US)"}
        )
        assert moved.status_code == 200
        assert moved.json()["location"] == "Remote (US)"

        discovered = client.post("/api/jobs/41/applications").json()
        refused = client.patch(
            f"/api/applications/{discovered['id']}", json={"location": "Remote (US)"}
        )
        assert refused.status_code == 422
        assert refused.json()["detail"] == "discovered application snapshots cannot be edited"


def test_the_resume_sent_is_recorded_on_any_application() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        discovered = client.post("/api/jobs/41/applications").json()
        assert discovered["resume_variant"] is None
        updated = client.patch(
            f"/api/applications/{discovered['id']}", json={"resume_variant": "Systems SWE"}
        )
        assert updated.status_code == 200
        assert updated.json()["resume_variant"] == "Systems SWE"


def test_jobs_default_scope_excludes_the_unclassified_pool() -> None:
    repository = FakeRepository()
    repository.jobs.append(
        {
            "id": 77,
            "company": "Example Labs",
            "title": "Senior Quantitative Analyst",
            "summary": None,
            "location": "New York",
            "apply_url": "https://jobs.example.test/77/apply",
            # NULL is how storage says "no vendor rule matched"; the API keeps
            # the `unclassified` token as the way to ask for that pool.
            "job_type": None,
            "attributes": {"version": 1},
        }
    )
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        default = client.get("/api/jobs").json()
        assert [job["id"] for job in default["jobs"]] == [41]
        review = client.get(
            "/api/jobs", params={"job_type": "unclassified"}
        ).json()
        assert [job["id"] for job in review["jobs"]] == [77]
        assert client.get(
            "/api/jobs", params={"job_type": "review"}
        ).status_code == 400


def test_api_rejects_unknown_job_filter_and_invalid_application_mutation() -> None:
    application = signed_in_app(repository=FakeRepository())
    with TestClient(application) as client:
        assert client.get(
            "/api/jobs",
            params=[
                ("job_type", "intern"),
                ("job_type", "coop"),
                ("job_type", "student"),
                ("job_type", "new_grad"),
                ("job_type", "research"),
            ],
        ).status_code == 200
        assert client.get("/api/jobs", params={"job_type": "made_up"}).status_code == 400
        assert client.get("/api/jobs", params={"degree_field": "made_up"}).status_code == 400
        assert client.get("/api/jobs", params={"season": "spring"}).status_code == 400
        assert client.get("/api/jobs", params={"season": "Fall"}).status_code == 200
        for domain in ("software", "finance", "unlabeled"):
            assert client.get(
                "/api/jobs", params={"degree_field": domain}
            ).status_code == 200
        created = client.post("/api/jobs/41/applications").json()
        assert client.patch(
            f"/api/applications/{created['id']}", json={"company": "Changed"}
        ).status_code == 422
        assert client.patch(
            f"/api/applications/{created['id']}", json={"stage": "closed"}
        ).status_code == 422


def test_discover_route_serves_the_standalone_document() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        response = client.get("/discover/")

    assert response.status_code == 200
    assert 'href="/static/discover/stub.css"' in response.text
    assert "standalone concept" in response.text
    assert "oneshot" not in response.text.lower()
    assert '<a ' not in response.text


def test_main_route_serves_the_opportunities_page() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/static/opportunities/styles.css?v=' in response.text
    assert 'src="/static/opportunities/app.js?v=' in response.text
    # The versioned directories are gone; nothing may still point at them.
    assert "opportunities-v2" not in response.text
    assert "opportunities-v3" not in response.text
    assert "(v2)" not in response.text


def test_page_shells_reference_assets_by_absolute_path() -> None:
    """A page shell served at a clean URL cannot use relative asset refs.

    Every shell is reachable at two depths -- /application/ and
    /static/application/index.html -- so "../navigation.css" resolves to
    /navigation.css under the first and /static/navigation.css under the
    second. Only one of those is served, so a relative ref silently breaks
    the canonical URL while the /static/ one keeps working.
    """
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        for path in ("/", "/application/", "/source-coverage/", "/resume/", "/login/"):
            text = client.get(path).text
            for ref in ('src="./', 'src="../', 'href="./', 'href="../'):
                assert ref not in text, f"{path} uses a relative asset ref {ref!r}"


def test_clean_page_urls_serve_the_same_shell_as_the_static_mount() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        for clean, mounted in (
            ("/", "/static/index.html"),
            ("/application/", "/static/application/index.html"),
            ("/source-coverage/", "/static/source-coverage/index.html"),
        ):
            assert client.get(clean).text == client.get(mounted).text
            # The nav must point at the canonical URL, not the mount path.
            assert mounted not in client.get("/").text


def test_every_browser_entrypoint_and_referenced_asset_is_served() -> None:
    # No dependency override and no cookie: the page shells and their assets are
    # public by design, as is everything they call. If this ever 401s or 302s,
    # that decision has been reversed by accident.
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    paths = (
        "/",
        "/static/api-client.js",
        "/static/nav-count.js",
        "/static/sample-data.js",
        "/static/navigation.css",
        "/static/brand-mark.svg",
        "/static/index.html",
        "/static/opportunities/styles.css",
        "/static/opportunities/app.js",
        "/application/",
        "/static/application/index.html",
        "/static/application/styles.css",
        "/static/application/app.js",
        "/source-coverage/",
        "/static/source-coverage/index.html",
        "/static/source-coverage/styles.css",
        "/static/source-coverage/app.js",
        "/resume/",
        "/static/resume/styles.css",
        "/static/resume/app.js",
        "/login/",
        "/static/login/styles.css",
        "/discover/",
        "/static/discover/stub.css",
    )
    with TestClient(application) as client:
        # One assertion per path: `all(...)` inside a single assert reports a bare
        # `assert False` and names nothing.
        for path in paths:
            assert client.get(path).status_code == 200, f"{path} is not served"


@pytest.mark.asyncio
async def test_coverage_reads_only_the_single_snapshot_row() -> None:
    class SnapshotPool:
        def __init__(self) -> None:
            self.queries = []

        async def fetchrow(self, query, *args):
            self.queries.append(query)
            return {
                "payload": (
                    '{"source_coverage":{"sources":[],"source_count":0,'
                    '"total_coverage":4,"total_added_today":1},'
                    '"board_map":{"providers":[],"totals":{"boards":2}}}'
                ),
                "computed_at": "2026-09-02T20:00:00+00:00",
            }

    pool = SnapshotPool()
    repository = PostgresRepository("injected", pool=pool)

    assert (await repository.source_coverage())["total_coverage"] == 4
    assert (await repository.board_map())["totals"]["boards"] == 2
    assert all("coverage_snapshot" in query for query in pool.queries)
    assert all("feed" not in query and "token" not in query for query in pool.queries)


def test_search_treats_ilike_wildcards_as_literal_text() -> None:
    """A typed ``%`` or ``_`` must match itself, not act as a wildcard.

    The API tests inject a repository that ignores ``q`` entirely and the SQL
    path is only reachable with a live database, so the escaping is pinned here
    at the level that has no such dependency. Backslash is PostgreSQL's default
    LIKE escape character, which is why it is escaped first — and why a trailing
    one must not be left bare, since a pattern ending in the escape character is
    an error rather than a miss.
    """
    assert _ilike_pattern("python") == "%python%"
    assert _ilike_pattern("C_") == r"%C\_%"
    assert _ilike_pattern("100%") == r"%100\%%"
    assert _ilike_pattern("a%b_c") == r"%a\%b\_c%"
    assert _ilike_pattern("\\") == r"%\\%"
    assert _ilike_pattern("ends\\") == r"%ends\\%"


@pytest.mark.asyncio
async def test_domain_filters_read_the_stored_gold_column() -> None:
    from oneshot_ui.repository import DOMAIN_FILTERS

    class QueryPool:
        def __init__(self):
            self.queries = []

        async def fetchval(self, query, *args):
            return 0

        async def fetchrow(self, query, *args):
            self.queries.append((query, args))
            return {
                "intern": 0,
                "coop": 0,
                "student": 0,
                "new_grad": 0,
                "research": 0,
            }

        async def fetch(self, query, *args):
            self.queries.append((query, args))
            return []

    pool = QueryPool()
    repository = PostgresRepository("injected", pool=pool)
    for domain in DOMAIN_FILTERS:
        await repository.list_jobs(
            job_types=["intern"], degree_fields=[domain], hide_tracked=False
        )

    sql = "\n".join(query for query, _args in pool.queries)
    assert sql.count("j.job_domain = ANY") == 2 * len(DOMAIN_FILTERS)
    assert "regexp_replace" not in sql
    assert "~*" not in sql
    bound_lists = [
        value
        for _query, args in pool.queries
        for value in args
        if isinstance(value, list)
    ]
    for domain in DOMAIN_FILTERS:
        assert [domain] in bound_lists


@pytest.mark.asyncio
async def test_job_type_counts_are_cached_across_tabs_and_repository_clones() -> None:
    class CountingPool:
        def __init__(self) -> None:
            self.count_reads = 0
            self.page_reads = 0

        async def fetchval(self, query, *args):
            raise AssertionError("standard job types derive total from tab counts")

        async def fetchrow(self, query, *args):
            self.count_reads += 1
            await asyncio.sleep(0)
            return {
                "intern": 5,
                "coop": 2,
                "student": 3,
                "new_grad": 4,
                "research": 1,
            }

        async def fetch(self, query, *args):
            self.page_reads += 1
            return []

    pool = CountingPool()
    repository = PostgresRepository("injected", pool=pool)
    account_repository = repository.for_account(TEST_ACCOUNT_ID)

    internships, coops = await asyncio.gather(
        repository.list_jobs(
            job_types=["intern"], degree_fields=["software"], hide_tracked=False
        ),
        account_repository.list_jobs(
            job_types=["coop"], degree_fields=["software"], hide_tracked=False
        ),
    )

    assert internships["total"] == 5
    assert coops["total"] == 2
    assert internships["job_type_counts"] == coops["job_type_counts"]
    assert pool.count_reads == 1
    assert pool.page_reads == 2

    # Search terms are unbounded user input, so they bypass the small cache.
    await repository.list_jobs(job_types=["intern"], q="python")
    await repository.list_jobs(job_types=["intern"], q="python")
    assert pool.count_reads == 3



def test_company_logo_route_serves_stored_bytes_and_404s_on_a_miss():
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        hit = client.get("/api/companies/7/logo")
        assert hit.status_code == 200
        assert hit.content == repository.logos[7]
        # Sniffed from the payload, because the bytes are stored exactly as the
        # company's own site served them and carry no recorded type.
        assert hit.headers["content-type"] == "image/png"
        # A list page asks for ~30 of these at once; without this every card
        # revalidates on every view.
        assert "max-age=" in hit.headers["cache-control"]

        # A recorded failure and an unknown company are the same answer: the
        # client already knows not to ask, so this is only the direct-hit case.
        assert client.get("/api/companies/8/logo").status_code == 404
        assert client.get("/api/companies/999/logo").status_code == 404


def test_logo_media_type_names_every_format_a_favicon_arrives_as():
    from oneshot_ui.app import _logo_media_type

    assert _logo_media_type(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert _logo_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert _logo_media_type(b"GIF89a") == "image/gif"
    assert _logo_media_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert _logo_media_type(b"\x00\x00\x01\x00\x01\x00") == "image/x-icon"
    # SVG is the only text format here, so it is the fallback rather than a
    # magic-number match.
    assert _logo_media_type(b'<svg xmlns="http://www.w3.org/2000/svg">') == "image/svg+xml"


def test_job_response_carries_the_focus_label_the_triage_pane_disputes() -> None:
    row = {
        "id": 41,
        "title": "Summer Software Engineering Intern",
        "company_name": "Example Labs",
        "locations": [],
        "attributes": {},
    }
    assert PostgresRepository._job({**row, "job_domain": "software"})["job_domain"] == "software"
    assert PostgresRepository._job(row)["job_domain"] is None


def test_skip_hides_a_posting_until_it_is_restored() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        assert client.get("/api/jobs").json()["total"] == 1

        skipped = client.put("/api/jobs/41/feedback", json={"kind": "skip"})
        assert skipped.status_code == 200
        assert skipped.json()["skip_reason"] is None
        assert client.get("/api/jobs").json()["total"] == 0

        # The reason arrives after the fact, from the toast, as a replace.
        reasoned = client.put(
            "/api/jobs/41/feedback", json={"kind": "skip", "skip_reason": "location"}
        ).json()
        assert reasoned["skip_reason"] == "location"

        hidden = client.get("/api/feedback").json()
        assert hidden["total"] == 1
        assert hidden["hidden"][0]["skip_reason"] == "location"
        assert hidden["hidden"][0]["job"]["title"] == "Summer Software Engineering Intern"

        assert client.delete("/api/jobs/41/feedback").status_code == 204
        assert client.get("/api/jobs").json()["total"] == 1
        assert client.delete("/api/jobs/41/feedback").status_code == 404
        assert client.put("/api/jobs/999/feedback", json={"kind": "skip"}).status_code == 404


def test_mislabeled_report_keeps_the_corrections_on_the_wire_names() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        report = client.put(
            "/api/jobs/41/feedback",
            json={
                "kind": "mislabeled",
                "corrections": [
                    {"field": "focus", "from": "Software", "to": "Finance"},
                    {"field": "company", "from": "hctz.fa.us2"},
                ],
            },
        )
        assert report.status_code == 200
        assert report.json()["corrections"] == [
            {"field": "focus", "from": "Software", "to": "Finance"},
            {"field": "company", "from": "hctz.fa.us2", "to": None},
        ]
        assert client.get("/api/jobs").json()["total"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "hide"},
        {"kind": "skip", "skip_reason": "salary"},
        {"kind": "skip", "corrections": [{"field": "focus", "from": "Software"}]},
        {"kind": "mislabeled"},
        {"kind": "mislabeled", "skip_reason": "level", "corrections": [{"field": "focus", "from": "Software"}]},
        {"kind": "mislabeled", "corrections": [{"field": "salary", "from": "x"}]},
        {"kind": "mislabeled", "corrections": [{"field": "company", "from": "x" * 301}]},
        {"kind": "mislabeled", "corrections": [{"field": "focus", "from": "x"}] * 6},
    ],
)
def test_feedback_rejects_a_reason_that_does_not_match_its_kind(body) -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        assert client.put("/api/jobs/41/feedback", json=body).status_code == 422
        assert repository.feedback == {}


@pytest.mark.asyncio
async def test_hidden_feedback_leaves_the_feed_with_its_duplicate_group() -> None:
    class QueryPool:
        def __init__(self):
            self.queries = []

        async def fetchval(self, query, *args):
            return 0

        async def fetchrow(self, query, *args):
            self.queries.append(query)
            return {"intern": 0, "coop": 0, "student": 0, "new_grad": 0, "research": 0}

        async def fetch(self, query, *args):
            self.queries.append(query)
            return []

    pool = QueryPool()
    repository = PostgresRepository("injected", pool=pool).for_account(TEST_ACCOUNT_ID)

    await repository.list_jobs(job_types=["intern"], hide_tracked=True)
    signed_in = "\n".join(pool.queries)
    assert "FROM job_feedback f" in signed_in
    assert "hidden.dup_group_id = j.dup_group_id" in signed_in

    pool.queries.clear()
    await repository.list_jobs(job_types=["intern"], hide_tracked=False)
    assert "job_feedback" not in "\n".join(pool.queries)
