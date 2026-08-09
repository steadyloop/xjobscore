from copy import deepcopy

from fastapi.testclient import TestClient

from oneshot_ui.app import create_app
from oneshot_ui.config import Settings


class FakeRepository:
    def __init__(self) -> None:
        self.connected = False
        self.jobs = [
            {
                "id": 41,
                "company": "Example Labs",
                "title": "Summer Software Engineering Intern",
                "description": "Build Python services.",
                "location": "Remote, United States",
                "apply_url": "https://jobs.example.test/41/apply",
                "attributes": {
                    "version": 1,
                    "opportunity_type": "summer_internship",
                    "term": "Summer 2027",
                    "workplace": "remote",
                    "skills": ["Python", "PostgreSQL"],
                    "eligibility": "eligible",
                },
            }
        ]
        self.applications: list[dict] = []
        self.next_id = 1

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def list_jobs(self, **options):
        application_job_ids = {row["job_id"] for row in self.applications if row["job_id"]}
        rows = [row for row in self.jobs if row["id"] not in application_job_ids]
        types = set(options["opportunity_types"])
        if types:
            rows = [row for row in rows if row["attributes"]["opportunity_type"] in types]
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

def test_live_api_contract_uses_repository_and_postgres_vocabulary() -> None:
    repository = FakeRepository()
    application = create_app(Settings(dsn="injected"), repository=repository)
    with TestClient(application) as client:
        assert repository.connected
        assert client.get("/api/health").json() == {"ok": True}
        jobs = client.get(
            "/api/jobs", params={"opportunity_type": "summer_internship"}
        ).json()
        assert jobs["total"] == 1
        assert jobs["jobs"][0]["attributes"]["skills"] == ["Python", "PostgreSQL"]
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


def test_api_rejects_unknown_job_filter_and_invalid_application_mutation() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    with TestClient(application) as client:
        assert client.get("/api/jobs", params={"opportunity_type": "made_up"}).status_code == 400
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


def test_every_browser_entrypoint_and_referenced_asset_is_served() -> None:
    application = create_app(Settings(dsn="injected"), repository=FakeRepository())
    paths = (
        "/",
        "/static/app.css",
        "/static/app.js",
        "/static/api-client.js",
        "/static/nav-count.js",
        "/static/sample-data.js",
        "/static/application/index.html",
        "/static/application/styles.css",
        "/static/application/app.js",
        "/static/source-coverage/index.html",
        "/static/source-coverage/styles.css",
        "/static/source-coverage/app.js",
        "/discover/",
        "/static/discover/stub.css",
    )
    with TestClient(application) as client:
        assert all(client.get(path).status_code == 200 for path in paths)
