import asyncio
from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from oneshot_ui.app import create_app
from oneshot_ui.auth import session_account
from oneshot_ui.config import Settings
from oneshot_ui.repository import PostgresRepository, _ilike_pattern

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
                "description": "Build Python services.",
                "location": "Remote, United States",
                "apply_url": "https://jobs.example.test/41/apply",
                "attributes": {
                    "version": 1,
                    "opportunity_type": "internship",
                    "term": "Summer 2027",
                    "workplace": "remote",
                    "skills": ["python", "postgresql"],
                    "degree_fields": ["computer_science"],
                },
                "skills": [
                    {"id": "python", "name": "Python", "category": "language"},
                    {"id": "postgresql", "name": "PostgreSQL", "category": "stack"},
                ],
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
        types = set(options["opportunity_types"])
        if types:
            rows = [row for row in rows if row["attributes"]["opportunity_type"] in types]
        degree_fields = set(options["degree_fields"])
        if degree_fields:
            rows = [
                row
                for row in rows
                if degree_fields.intersection(row["attributes"].get("degree_fields", []))
            ]
        return {"jobs": deepcopy(rows), "total": len(rows), "limit": options["limit"], "offset": options["offset"]}

    async def source_coverage(self):
        return {
            "sources": [{"source": "ashby", "type": "ATS", "board_count": 1, "coverage": 1, "last_poll_at": None, "health": "Not polled", "added_today": 1}],
            "source_count": 1,
            "total_coverage": 1,
            "total_added_today": 1,
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
        "description": "Build Python services.",
        "location_text": "Remote, United States",
        "apply_url": "https://jobs.example.test/41/apply",
        "posted_at": None,
        "first_seen_at": None,
        "last_seen_at": None,
        "status": "active",
        "attributes": {"version": 1},
        "content_hash": "internal-hash",
        "raw_id": 99,
        "source_key": "internal-key",
    })

    assert result["source"] == "greenhouse:example-labs"
    assert result["source_url"] == "https://jobs.example.test/41"
    assert result["skills"] == []
    assert not ({"content_hash", "raw_id", "source_key"} & result.keys())


def test_job_response_derives_structured_skills_without_mutating_attributes() -> None:
    stored = ["go", "Kubernetes", "legacy-tool", "go", "", 7]
    result = PostgresRepository._job({
        "id": 42,
        "title": "Backend Engineer",
        "attributes": {"version": 1, "skills": stored},
    })

    assert result["attributes"]["skills"] == stored
    assert result["skills"] == [
        {"id": "go", "name": "Go", "category": "language"},
        {"id": "kubernetes", "name": "Kubernetes", "category": "stack"},
        {"id": "legacy-tool", "name": "legacy-tool", "category": None},
    ]


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
            self.page_args = ()

        async def fetchval(self, query, *args):
            self.count_args = args
            await self.page_started.wait()
            return 1

        async def fetch(self, query, *args):
            self.page_args = args
            self.page_started.set()
            return [
                {
                    "id": 41,
                    "source": "greenhouse:example-labs",
                    "source_url": "https://jobs.example.test/41",
                    "company_name": "Example Labs",
                    "title": "Summer Software Engineering Intern",
                    "description": "Build Python services.",
                    "location_text": "Remote, United States",
                    "apply_url": "https://jobs.example.test/41/apply",
                    "posted_at": None,
                    "first_seen_at": None,
                    "last_seen_at": None,
                    "status": "active",
                    "attributes": {
                        "version": 1,
                        "opportunity_type": "internship",
                    },
                }
            ]

    pool = GatedPool()
    repository = PostgresRepository("injected", pool=pool)

    result = await asyncio.wait_for(
        repository.list_jobs(
            opportunity_types=["internship"],
            degree_fields=["computer_science"],
            exclude_job_ids=[99],
            limit=25,
            offset=50,
        ),
        timeout=1,
    )

    assert result["total"] == 1
    assert result["jobs"][0]["id"] == 41
    # COUNT sees only account + filters; LIMIT/OFFSET belong only to the page.
    assert len(pool.count_args) == 4
    assert pool.count_args[2] == ["computer_science"]
    assert pool.count_args[3] == [99]
    assert pool.page_args[-2:] == (25, 50)

def test_live_api_contract_uses_repository_and_postgres_vocabulary() -> None:
    repository = FakeRepository()
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        assert repository.connected
        assert client.get("/api/health").json() == {"ok": True}
        jobs = client.get(
            "/api/jobs",
            params={"opportunity_type": "internship", "degree_field": "computer_science"},
        ).json()
        assert jobs["total"] == 1
        assert jobs["jobs"][0]["attributes"]["skills"] == ["python", "postgresql"]
        assert jobs["jobs"][0]["attributes"]["degree_fields"] == ["computer_science"]
        assert jobs["jobs"][0]["skills"] == [
            {"id": "python", "name": "Python", "category": "language"},
            {"id": "postgresql", "name": "PostgreSQL", "category": "stack"},
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
    assert not repository.connected


def test_jobs_default_scope_excludes_the_unclassified_pool() -> None:
    repository = FakeRepository()
    repository.jobs.append(
        {
            "id": 77,
            "company": "Example Labs",
            "title": "Senior Quantitative Analyst",
            "description": "4+ years of experience.",
            "location": "New York",
            "apply_url": "https://jobs.example.test/77/apply",
            "attributes": {"version": 1, "opportunity_type": "unclassified"},
        }
    )
    application = signed_in_app(repository=repository)
    with TestClient(application) as client:
        default = client.get("/api/jobs").json()
        assert [job["id"] for job in default["jobs"]] == [41]
        review = client.get(
            "/api/jobs", params={"opportunity_type": "unclassified"}
        ).json()
        assert [job["id"] for job in review["jobs"]] == [77]
        assert client.get(
            "/api/jobs", params={"opportunity_type": "review"}
        ).status_code == 400


def test_api_rejects_unknown_job_filter_and_invalid_application_mutation() -> None:
    application = signed_in_app(repository=FakeRepository())
    with TestClient(application) as client:
        assert client.get(
            "/api/jobs",
            params=[
                ("opportunity_type", "internship"),
                ("opportunity_type", "coop"),
                ("opportunity_type", "apprenticeship"),
                ("opportunity_type", "new_grad_program"),
            ],
        ).status_code == 200
        assert client.get("/api/jobs", params={"opportunity_type": "made_up"}).status_code == 400
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
