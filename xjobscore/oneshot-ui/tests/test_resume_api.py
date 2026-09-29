import asyncio
import re
from uuid import UUID

from fastapi.testclient import TestClient

from fake_resume_store import InMemoryResumeStore

from oneshot_ui.app import create_app
from oneshot_ui.auth import session_account
from oneshot_ui.config import Settings
from oneshot_ui.resume_render import build_typst_source

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


def typst_source(client: TestClient, resume_id: int) -> str:
    """The Typst source a stored resume compiles from."""
    store = client.app.state.resume_store.for_account(TEST_ACCOUNT_ID)
    return build_typst_source(*asyncio.run(store.render_inputs(resume_id)))


def seed_bank(client: TestClient) -> tuple[int, int, int]:
    client.put("/api/resume/profile", json={
        "full_name": "Ada Lovelace",
        "email": "ada@example.test",
        "links": [{"label": "GitHub", "url": "https://github.com/ada"}],
    })
    # Variants are per-account and start empty, so anything tagged with one has
    # to declare it first. This is the studio's real order of operations.
    client.put("/api/resume/variants", json={
        "variants": [{"key": "swe", "label": "Systems SWE"}],
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
        entry_id, bullet_id, skill_id = seed_bank(client)
        entries = client.get("/api/resume/entries").json()["entries"]
        assert entries[0]["id"] == entry_id
        assert entries[0]["bullets"][0]["tags"] == ["python"]

        assert client.post("/api/resume/skills", json={
            "category": "language", "name": "Python",
        }).status_code == 409

        patched = client.patch(f"/api/resume/bullets/{bullet_id}", json={"audience": "all"})
        assert patched.json()["audience"] == "all"

        patched_skill = client.patch(f"/api/resume/skills/{skill_id}", json={"name": "Python 3"})
        assert patched_skill.status_code == 200
        assert patched_skill.json()["name"] == "Python 3"

        assert client.delete(f"/api/resume/entries/{entry_id}").status_code == 204
        assert client.get("/api/resume/entries").json()["entries"] == []


def test_resume_source():
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

        source = typst_source(client, resume["id"])
        assert "Ada Lovelace" in source
        assert "2,600+ feeds" in source
        assert "*Languages:* Python" in source


def test_education_is_fixed_and_selected_coursework_renders_beneath_it():
    with make_client() as client:
        education = client.post("/api/resume/entries", json={
            "kind": "education",
            "org": "University of Example",
            "role": "B.S. Computer Science",
            "end_date": "Jun 2027",
            "detail": "3.82",
        }).json()
        course = client.post("/api/resume/skills", json={
            "category": "coursework", "name": "Operating Systems",
        }).json()
        resume = client.post("/api/resume/resumes", json={
            "name": "Internship",
            "selection": {"entries": [], "skills": [course["id"]]},
        }).json()

        source = typst_source(client, resume["id"])
        assert education["org"] in source
        assert "*Relevant Coursework:* Operating Systems" in source


def test_a_resume_can_leave_the_gpa_off():
    with make_client() as client:
        education = client.post("/api/resume/entries", json={
            "kind": "education", "org": "University of Example",
            "role": "B.S. Computer Science", "detail": "GPA 3.82",
        }).json()
        resume = client.post("/api/resume/resumes", json={
            "name": "Internship", "availability_line": "Available Spring 2027",
            "selection": {"entries": [{"entry_id": education["id"], "show_detail": False}], "skills": []},
        }).json()
        assert resume["selection"]["entries"][0]["show_detail"] is False

        source = typst_source(client, resume["id"])
        assert education["org"] in source
        assert "3.82" not in source
        assert "Available Spring 2027" in source

        shown = client.patch(f"/api/resume/resumes/{resume['id']}", json={
            "selection": {"entries": [{"entry_id": education["id"]}], "skills": []},
        }).json()
        assert shown["selection"]["entries"][0].get("show_detail", True) is True  # absent means shown
        assert "3.82" in typst_source(client, resume["id"])


def test_barlow_rewords_headers_per_resume_and_keeps_picked_skill_order():
    with make_client() as client:
        entry = client.post("/api/resume/entries", json={
            "kind": "project", "org": "Bank Title", "role": "Bank Role", "start_date": "Apr 2026",
        }).json()
        bullet = client.post(f"/api/resume/entries/{entry['id']}/bullets", json={"text": "Shipped it"}).json()
        go = client.post("/api/resume/skills", json={"category": "language", "name": "Go"}).json()
        c = client.post("/api/resume/skills", json={"category": "language", "name": "C"}).json()
        pick = {"entry_id": entry["id"], "org": "Resume Title", "start_date": "",
                "bullets": [{"bullet_id": bullet["id"]}]}
        resume = client.post("/api/resume/resumes", json={
            "name": "Sans", "template": "barlow",
            "selection": {"entries": [pick], "skills": [go["id"], c["id"]]},
        }).json()
        assert resume["template"] == "barlow"

        source = typst_source(client, resume["id"])
        assert 'font: "Barlow"' in source
        assert "Resume Title" in source and "Bank Title" not in source
        assert "Bank Role" in source      # no override: the bank's value
        assert "Apr 2026" not in source   # "" override: blank on this resume
        assert "Go, C" in source          # picked order, not alphabetical

        client.patch(f"/api/resume/resumes/{resume['id']}", json={"template": "main"})
        assert 'font: "Barlow"' not in typst_source(client, resume["id"])


def test_a_url_cannot_break_out_of_its_typst_string():
    """A URL is interpolated into #link("…"), so a quote in it must not escape.

    Before this was escaped, a URL containing `")` closed the link call and the
    rest of the value compiled as Typst markup rather than printing as a link.
    It compiled *cleanly*, which is why nothing caught it: the check has to be
    that no unescaped quote survives, not that the document still builds.
    """
    attack = 'https://x.test/") + text(fill: red)[PWNED] + link("https://y'
    with make_client() as client:
        entry_id, bullet_id, _ = seed_bank(client)
        client.put("/api/resume/profile", json={
            "email": "ada@example.test",
            "links": [{"label": "GitHub", "url": attack}],
        })
        client.patch(f"/api/resume/entries/{entry_id}", json={"url": attack})
        resume = client.post("/api/resume/resumes", json={
            "name": "Injected",
            "selection": {"entries": [{"entry_id": entry_id,
                                       "bullets": [{"bullet_id": bullet_id}]}]},
        }).json()

        source = typst_source(client, resume["id"])
        # Both call sites: the profile link in the header and the entry's link.
        assert source.count('\\") + text') == 2
        assert re.search(r'(?<!\\)"\) \+ text', source) is None

        response = client.get(f"/api/resume/resumes/{resume['id']}/pdf")
        assert response.status_code == 200
        assert response.headers["x-page-count"] == "1"


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
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-page-count"] == "1"
        assert response.content.startswith(b"%PDF")


def public_client() -> TestClient:
    """No session override: exactly what a signed-out browser gets."""
    return TestClient(create_app(
        Settings(dsn=""),
        repository=NullRepository(),
        resume_store=InMemoryResumeStore(),
    ))


RENDER_PAYLOAD = {
    "profile": {"full_name": "Ada Lovelace", "email": "ada@example.test", "links": []},
    "resume": {
        "name": "Systems SWE",
        "variant": "swe",
        "availability_line": "Available Summer 2027",
        "education_note": "",
        "selection": {"entries": [{"entry_id": 1, "bullets": [{"bullet_id": 1}]}], "skills": [1]},
    },
    "entries": [{
        "id": 1, "kind": "project", "org": "Job posting pipeline", "role": "Solo developer",
        "start_date": "May 2026", "end_date": "Present",
        "bullets": [{"id": 1, "text": "Built an async pipeline ingesting 2,600+ feeds"}],
    }],
    "skills": [{"id": 1, "category": "language", "name": "Python"}],
}


def test_rendering_a_resume_needs_no_session() -> None:
    with public_client() as client:
        # The stored-resume routes stay gated; only rendering opens up.
        assert client.get("/api/resume/profile").status_code == 401
        assert client.get("/api/resume/resumes").status_code == 401

        response = client.post("/api/resume/render/pdf", json=RENDER_PAYLOAD)
        assert response.status_code == 200
        assert response.content[:4] == b"%PDF"
        assert response.headers["X-Page-Count"] == "1"


def test_rendering_refuses_an_oversized_resume() -> None:
    with public_client() as client:
        response = client.post("/api/resume/render/pdf", json={
            **RENDER_PAYLOAD,
            "profile": {"full_name": "x" * 300_000, "email": "ada@example.test"},
        })
        assert response.status_code == 413


# -- user-defined variants -----------------------------------------------


def test_variants_start_empty_and_round_trip() -> None:
    with make_client() as client:
        assert client.get("/api/resume/variants").json() == {"variants": []}
        response = client.put("/api/resume/variants", json={
            "variants": [
                {"key": "Regulatory", "label": "Regulatory Affairs"},
                {"key": "clinical", "label": ""},
            ],
        })
        assert response.status_code == 200
        # Keys lowercase for stable matching against a bullet's audience; a
        # blank label falls back to the key rather than rendering as empty.
        assert response.json()["variants"] == [
            {"key": "regulatory", "label": "Regulatory Affairs"},
            {"key": "clinical", "label": "clinical"},
        ]


def test_the_reserved_all_audience_cannot_be_declared_as_a_variant() -> None:
    with make_client() as client:
        client.put("/api/resume/variants", json={"variants": [{"key": "all"}]})
        # Accepting it would make every bullet match every resume.
        assert client.get("/api/resume/variants").json() == {"variants": []}


def test_an_undeclared_audience_is_refused() -> None:
    with make_client() as client:
        client.put("/api/resume/variants", json={"variants": [{"key": "biotech"}]})
        entry = client.post("/api/resume/entries", json={
            "kind": "experience", "org": "Genentech",
        }).json()
        rejected = client.post(f"/api/resume/entries/{entry['id']}/bullets", json={
            "text": "Ran assay validation across three sites", "audience": "quant",
        })
        assert rejected.status_code == 422
        assert "biotech" in rejected.json()["detail"]
        accepted = client.post(f"/api/resume/entries/{entry['id']}/bullets", json={
            "text": "Ran assay validation across three sites", "audience": "biotech",
        })
        assert accepted.status_code == 201


def test_a_resume_cannot_name_an_undeclared_variant() -> None:
    with make_client() as client:
        assert client.post("/api/resume/resumes", json={
            "name": "Genentech QA", "variant": "swe",
        }).status_code == 422
        # "" is always legal: it means no variant, which is the only sensible
        # state for an account that has not defined any.
        assert client.post("/api/resume/resumes", json={"name": "Draft"}).status_code == 201


def test_removing_a_variant_still_in_use_is_refused() -> None:
    with make_client() as client:
        client.put("/api/resume/variants", json={"variants": [{"key": "biotech"}]})
        entry = client.post("/api/resume/entries", json={
            "kind": "experience", "org": "Genentech",
        }).json()
        client.post(f"/api/resume/entries/{entry['id']}/bullets", json={
            "text": "Ran assay validation across three sites", "audience": "biotech",
        })
        response = client.put("/api/resume/variants", json={"variants": []})
        assert response.status_code == 409
        assert "biotech (1)" in response.json()["detail"]
        # The refusal must not have partially applied.
        assert [v["key"] for v in client.get("/api/resume/variants").json()["variants"]] == ["biotech"]


# -- the application handoff ---------------------------------------------


def test_a_queued_application_becomes_a_resume_target() -> None:
    store = InMemoryResumeStore()
    application = create_app(
        Settings(dsn=""), repository=NullRepository(), resume_store=store,
    )
    application.dependency_overrides[session_account] = lambda: TEST_ACCOUNT_ID
    store.for_account(TEST_ACCOUNT_ID).add_application(
        7, company="Medtronic", title="Quality Engineer",
        url="https://jobs.example.test/7", jd_text="GMP experience required.",
    )
    with TestClient(application) as client:
        created = client.post("/api/resume/jobs/from-application/7")
        assert created.status_code == 201
        target = created.json()
        assert target["company"] == "Medtronic"
        assert target["title"] == "Quality Engineer"
        assert target["jd_text"] == "GMP experience required."
        assert target["job_application_id"] == 7

        # Idempotent: pressing the button twice must not fork the target.
        again = client.post("/api/resume/jobs/from-application/7")
        assert again.json()["id"] == target["id"]
        assert len(client.get("/api/resume/jobs").json()["jobs"]) == 1


def test_an_application_that_is_not_yours_is_a_404() -> None:
    with make_client() as client:
        assert client.post("/api/resume/jobs/from-application/999").status_code == 404
