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
    _board_state,
    _ilike_pattern,
    _location_label,
    _skill_search_ids,
    _us_location_bucket,
    _us_proximity_priority,
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
                "remote": "yes",
                "attributes": {
                    "version": 1,
                    "term": "Summer 2027",
                    "skills": ["python", "postgresql"],
                    "degree_fields": ["computer_science"],
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
        rows = [row for row in self.jobs if row["id"] not in application_job_ids]
        degree_fields = set(options["degree_fields"])
        if degree_fields:
            rows = [
                row
                for row in rows
                if degree_fields.intersection(row["attributes"].get("degree_fields", []))
            ]
        job_type_counts = {
            job_type: sum(row.get("job_type") == job_type for row in rows)
            for job_type in ("intern", "coop", "student_program", "research", "part_time")
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
        if not row["external"] and any(key in changes for key in ("company", "title", "apply_url_snapshot")):
            raise ValueError("discovered application snapshots cannot be edited")
        if "company" in changes:
            row["company"] = changes["company"]
        if "title" in changes:
            row["title"] = changes["title"]
        for key in ("stage", "closed_reason", "apply_url_snapshot", "submitted_at", "next_action", "next_due_at", "note"):
            if key in changes:
                row[key] = changes[key]
        return deepcopy(row)

    async def delete_application(self, application_id):
        before = len(self.applications)
        self.applications = [row for row in self.applications if row["id"] != application_id]
        return len(self.applications) != before


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


def test_us_location_policy_has_four_read_time_buckets() -> None:
    assert _us_location_bucket([
        {"country": "US", "state": "CA", "city": "San Francisco"}
    ]) == 1
    assert _us_location_bucket([
        {"country": "US", "state": "CA", "city": "Palo Alto"}
    ]) == 2
    assert _us_location_bucket([
        {"country": "US", "state": "MN", "city": "Rochester"}
    ]) == 3
    assert _us_proximity_priority([
        {"country": "US", "state": "CA", "city": "San Francisco"}
    ]) == 1
    assert _us_proximity_priority([
        {"country": "US", "state": "CA", "city": "Los Angeles"}
    ]) == 2
    assert _us_proximity_priority([
        {"country": "US", "state": "WA", "city": "Seattle"}
    ]) == 3
    assert _us_proximity_priority([
        {"country": "US", "state": "NY", "city": "New York City"}
    ]) == 4
    assert _us_location_bucket([]) == 4
    assert _us_location_bucket([
        {"country": "DE", "state": "16", "city": "Berlin"}
    ]) is None
    assert _us_location_bucket([
        {"country": "DE", "state": "16", "city": "Berlin"},
        {"country": "US", "state": "AZ", "city": "Tucson"},
    ]) == 3


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
            self.type_count_query = ""
            self.page_args = ()
            self.page_query = ""

        async def fetchval(self, query, *args):
            self.count_query = query
            self.count_args = args
            await self.page_started.wait()
            return 1

        async def fetchrow(self, query, *args):
            self.type_count_query = query
            return {"intern": 1, "coop": 0, "student_program": 0, "research": 0, "part_time": 0}

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
            degree_fields=["computer_science"],
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
    assert len(pool.count_args) == 6
    assert pool.count_args[1] == ["computer_science"]
    assert pool.count_args[2] == [99]
    assert pool.count_args[3] == "%Kubernetes%"
    assert "kubernetes" in pool.count_args[4]
    assert pool.count_args[5] == ["intern"]
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
    for query in (pool.count_query, pool.type_count_query, pool.page_query):
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
                "student_program": 0,
                "research": 0,
                "part_time": 0,
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

    assert len(pool.queries) == 4
    employer_query, employer_args = pool.queries[0]
    assert "WITH company_postings AS MATERIALIZED" in employer_query
    assert "c.labels ? 'biotech'" in employer_query
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
    assert len(pool.queries) == 7


def test_live_api_contract_uses_repository_and_postgres_vocabulary() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        assert repository.connected
        assert client.get("/api/health").json() == {"ok": True}
        jobs = client.get(
            "/api/jobs",
            params={"job_type": "intern", "degree_field": "computer_science"},
        ).json()
        assert jobs["total"] == 1
        assert jobs["job_type_counts"] == {
            "intern": 1, "coop": 0, "student_program": 0, "research": 0, "part_time": 0,
        }
        assert jobs["jobs"][0]["attributes"]["skills"] == ["python", "postgresql"]
        assert jobs["jobs"][0]["attributes"]["degree_fields"] == ["computer_science"]
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
                ("job_type", "student_program"),
                ("job_type", "research"),
            ],
        ).status_code == 200
        assert client.get("/api/jobs", params={"job_type": "made_up"}).status_code == 400
        assert client.get("/api/jobs", params={"degree_field": "made_up"}).status_code == 400
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


def test_main_route_serves_the_v2_opportunities_design() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'href="/static/opportunities-v2/styles.css?v=' in response.text
    assert 'src="/static/opportunities-v2/app.js?v=' in response.text
    assert "Opportunities V2" not in response.text


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
        for path in ("/", "/opportunities-v2/", "/application/", "/source-coverage/", "/resume/", "/login/"):
            text = client.get(path).text
            for ref in ('src="./', 'src="../', 'href="./', 'href="../'):
                assert ref not in text, f"{path} uses a relative asset ref {ref!r}"


def test_clean_page_urls_serve_the_same_shell_as_the_static_mount() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        for clean, mounted in (
            ("/opportunities-v2/", "/static/opportunities-v2/index.html"),
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
        "/static/app.css",
        "/static/app.js",
        "/static/api-client.js",
        "/static/nav-count.js",
        "/static/sample-data.js",
        "/static/navigation.css",
        "/static/brand-mark.svg",
        "/opportunities-v2/",
        "/static/opportunities-v2/index.html",
        "/static/opportunities-v2/styles.css",
        "/static/opportunities-v2/app.js",
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


def test_board_state_ranks_deliberate_holds_above_derived_signals() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=5)
    ancient = now - timedelta(days=2)
    # An operator hold outranks strikes; repeated failures outrank staleness.
    assert _board_state(5, ancient, now, 900, now) == "paused"
    assert _board_state(3, ancient, None, 900, now) == "failing"
    assert _board_state(1, fresh, None, 900, now) == "degraded"
    assert _board_state(0, None, None, 900, now) == "never"
    assert _board_state(0, ancient, None, 900, now) == "stale"
    assert _board_state(0, fresh, None, 900, now) == "ok"
    # No cadence configured: staleness cannot be judged, so a polled feed is ok.
    assert _board_state(0, ancient, None, 0, now) == "ok"


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
