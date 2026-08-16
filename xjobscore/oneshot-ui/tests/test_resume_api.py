from uuid import UUID

from fastapi.testclient import TestClient

from fake_resume_store import InMemoryResumeStore

from oneshot_ui.app import create_app
from oneshot_ui.auth import session_account
from oneshot_ui.config import Settings
from oneshot_ui.resume_render import typst_available

TEST_ACCOUNT_ID = UUID("00000000-0000-0000-0000-0000000000aa")


class NullRepository:
    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def for_account(self, account_id):
        return self


def make_client() -> TestClient:
    application = create_app(
        Settings(dsn=""),
        repository=NullRepository(),
        resume_store=InMemoryResumeStore(),
    )
    application.dependency_overrides[session_account] = lambda: TEST_ACCOUNT_ID
    return TestClient(application)


def seed_bank(client: TestClient) -> tuple[int, int, int]:
    client.put("/api/resume/profile", json={
        "full_name": "Ada Lovelace",
        "email": "ada@example.test",
        "links": [{"label": "GitHub", "url": "https://github.com/ada"}],
    })
    entry = client.post("/api/resume/entries", json={
        "kind": "project",
        "org": "Job posting pipeline",
        "role": "Solo developer",
        "start_date": "May 2026",
        "end_date": "Present",
    }).json()
    bullet = client.post(f"/api/resume/entries/{entry['id']}/bullets", json={
        "text": "Built an async pipeline (Python, Postgres) ingesting 2,600+ feeds",
        "audience": "swe",
        "tags": ["python"],
        "defense_notes": "asyncpg pool, 6 ATS formats",
    }).json()
    skill = client.post("/api/resume/skills", json={
        "category": "language", "name": "Python",
    }).json()
    return entry["id"], bullet["id"], skill["id"]


def test_profile_roundtrip():
    with make_client() as client:
        response = client.put("/api/resume/profile", json={"full_name": "Ada Lovelace"})
        assert response.status_code == 200
        assert client.get("/api/resume/profile").json()["full_name"] == "Ada Lovelace"


def test_bank_crud_and_duplicate_skill():
    with make_client() as client:
        entry_id, bullet_id, _ = seed_bank(client)
        entries = client.get("/api/resume/entries").json()["entries"]
        assert entries[0]["id"] == entry_id
        assert entries[0]["bullets"][0]["tags"] == ["python"]

        assert client.post("/api/resume/skills", json={
            "category": "language", "name": "Python",
        }).status_code == 409

        patched = client.patch(f"/api/resume/bullets/{bullet_id}", json={"audience": "all"})
        assert patched.json()["audience"] == "all"

        assert client.delete(f"/api/resume/entries/{entry_id}").status_code == 204
        assert client.get("/api/resume/entries").json()["entries"] == []


def test_coverage_flags_matched_gap_and_missing():
    with make_client() as client:
        _, _, skill_id = seed_bank(client)
        job = client.post("/api/resume/jobs", json={
            "company": "Example Labs",
            "title": "SWE Intern",
            "jd_text": "We want Python and Rust experience, plus Monte Carlo methods.",
        }).json()

        coverage = client.get(f"/api/resume/jobs/{job['id']}/coverage").json()
        assert [s["name"] for s in coverage["have_not_shown"]] == ["Python"]
        assert "Rust" in coverage["missing"]
        assert "Monte Carlo" in coverage["missing"]

        resume = client.post("/api/resume/resumes", json={
            "name": "SWE",
            "target_job_id": job["id"],
            "selection": {"entries": [], "skills": [skill_id]},
        }).json()
        coverage = client.get(
            f"/api/resume/jobs/{job['id']}/coverage",
            params={"resume_id": resume["id"]},
        ).json()
        assert [s["name"] for s in coverage["matched"]] == ["Python"]
        assert coverage["have_not_shown"] == []


def test_resume_source_and_checks():
    with make_client() as client:
        entry_id, bullet_id, skill_id = seed_bank(client)
        resume = client.post("/api/resume/resumes", json={
            "name": "SWE — Summer 2027",
            "availability_line": "Available for Summer 2027 internships",
            "selection": {
                "entries": [{"entry_id": entry_id,
                             "bullets": [{"bullet_id": bullet_id}]}],
                "skills": [skill_id],
            },
        }).json()

        source = client.get(f"/api/resume/resumes/{resume['id']}/source").text
        assert "Ada Lovelace" in source
        assert "2,600+ feeds" in source
        assert "*Languages:* Python" in source

        check = client.get(f"/api/resume/resumes/{resume['id']}/check").json()
        levels = {problem["level"] for problem in check["problems"]}
        assert "error" not in levels
        if check["typst_available"]:
            assert check["page_count"] == 1


def test_lint_catches_filler_and_missing_bullets():
    with make_client() as client:
        entry_id, _, _ = seed_bank(client)
        weak = client.post(f"/api/resume/entries/{entry_id}/bullets", json={
            "text": "Responsible for various tasks on the team",
        }).json()
        resume = client.post("/api/resume/resumes", json={
            "name": "Weak",
            "selection": {"entries": [{"entry_id": entry_id,
                                       "bullets": [{"bullet_id": weak["id"]}]}]},
        }).json()
        messages = [
            problem["message"]
            for problem in client.get(f"/api/resume/resumes/{resume['id']}/check").json()["problems"]
        ]
        assert any("responsible for" in message for message in messages)
        assert any("availability" in message.lower() for message in messages)

        empty = client.post("/api/resume/resumes", json={"name": "Empty"}).json()
        problems = client.get(f"/api/resume/resumes/{empty['id']}/check").json()["problems"]
        assert any(problem["message"] == "No bullets selected." for problem in problems)


def test_pdf_export():
    with make_client() as client:
        entry_id, bullet_id, skill_id = seed_bank(client)
        resume = client.post("/api/resume/resumes", json={
            "name": "SWE",
            "selection": {
                "entries": [{"entry_id": entry_id,
                             "bullets": [{"bullet_id": bullet_id}]}],
                "skills": [skill_id],
            },
        }).json()
        response = client.get(f"/api/resume/resumes/{resume['id']}/pdf")
        if typst_available():
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert response.headers["x-page-count"] == "1"
            assert response.content.startswith(b"%PDF")
        else:
            assert response.status_code == 503
