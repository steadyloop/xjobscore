"""What only a real database can prove about the resume store.

Opt in with XJOBSCORE_TEST_DSN. Everything here is either a PostgreSQL behaviour
(jsonb decoding, ON DELETE SET NULL, row locking) or a cross-account guarantee a
fake cannot honestly make.
"""

import asyncio
import os
from uuid import uuid4

import pytest

from oneshot_ui.resume_store import PostgresResumeStore

# Marks every row this module creates outside the account cascade, so teardown
# can sweep them without guessing.
_TEST_SOURCE = "resume-postgres-test"

pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


@pytest.fixture
async def store():
    """A store bound to a throwaway account.

    Teardown is one DELETE: ON DELETE CASCADE from `account` sweeps the
    workspace, targets, and documents with it.
    """
    base = PostgresResumeStore(os.environ["XJOBSCORE_TEST_DSN"])
    await base.connect()
    accounts = [uuid4(), uuid4()]
    try:
        for account_id in accounts:
            await base._ready().execute(
                "INSERT INTO account (id, name) VALUES ($1, 'resume test')", account_id
            )
        yield base.for_account(accounts[0]), base.for_account(accounts[1])
    finally:
        for account_id in accounts:
            # job_application.account_id has no ON DELETE CASCADE -- deliberately,
            # so a stray delete cannot silently take an application history with
            # it. That means the tracker rows have to go first here.
            await base._ready().execute(
                "DELETE FROM job_application WHERE account_id = $1", account_id
            )
            await base._ready().execute("DELETE FROM account WHERE id = $1", account_id)
        await base._ready().execute(
            "DELETE FROM job_posting WHERE source = $1", _TEST_SOURCE
        )
        await base._ready().execute(
            "DELETE FROM raw_posting WHERE source = $1", _TEST_SOURCE
        )
        await base._ready().execute(
            "DELETE FROM company WHERE domain LIKE $1", f"{_TEST_SOURCE}%"
        )
        await base.close()


async def test_jsonb_fields_round_trip_as_python_objects(store) -> None:
    """The port's highest-risk failure: asyncpg hands back jsonb as a str.

    If this regresses, `selection` reaches the builder as a string, every
    checkbox renders unchecked, and the next save writes an empty selection over
    the user's real one — silently.
    """
    mine, _ = store
    await mine.update_profile(
        {"full_name": "Ada", "links": [{"label": "GitHub", "url": "https://gh/ada"}]}
    )
    entry = await mine.create_entry({"kind": "project", "org": "Pipeline"})
    bullet = await mine.create_bullet(entry["id"], {"text": "Shipped it", "tags": ["python"]})
    skill = await mine.create_skill({"category": "language", "name": "Python"})
    resume = await mine.create_resume({
        "name": "SWE",
        "selection": {
            "entries": [{"entry_id": entry["id"], "bullets": [{"bullet_id": bullet["id"]}]}],
            "skills": [skill["id"]],
        },
    })

    profile = await mine.get_profile()
    assert isinstance(profile["links"], list)
    assert profile["links"][0]["label"] == "GitHub"

    entries = await mine.list_entries()
    assert isinstance(entries[0]["bullets"][0]["tags"], list)

    fetched = await mine.get_resume(resume["id"])
    assert isinstance(fetched["selection"], dict)
    assert fetched["selection"]["skills"] == [skill["id"]]
    assert fetched["selection"]["entries"][0]["bullets"][0]["bullet_id"] == bullet["id"]


async def test_reading_a_fresh_account_creates_no_row(store) -> None:
    mine, _ = store
    assert (await mine.get_profile())["full_name"] == ""
    assert await mine.list_entries() == []

    exists = await mine._ready().fetchval(
        "SELECT count(*) FROM resume_workspace WHERE account_id = $1", mine.account_id
    )
    assert exists == 0, "a GET must not write a workspace row"

    await mine.create_entry({"kind": "project", "org": "First"})
    exists = await mine._ready().fetchval(
        "SELECT count(*) FROM resume_workspace WHERE account_id = $1", mine.account_id
    )
    assert exists == 1


async def test_accounts_cannot_see_or_touch_each_others_data(store) -> None:
    """The guarantee the whole auth change exists to provide."""
    mine, theirs = store
    entry = await mine.create_entry({"kind": "project", "org": "Mine"})
    await mine.create_skill({"category": "language", "name": "Python"})
    job = await mine.create_target_job({"company": "Acme", "title": "Intern"})
    resume = await mine.create_resume({"name": "Mine"})

    # Invisible.
    assert await theirs.list_entries() == []
    assert await theirs.list_skills() == []
    assert await theirs.list_target_jobs() == []
    assert await theirs.list_resumes() == []
    assert await theirs.get_resume(resume["id"]) is None

    # Untouchable, by id, even knowing it.
    assert await theirs.update_entry(entry["id"], {"org": "Stolen"}) is None
    assert await theirs.delete_entry(entry["id"]) is False
    assert await theirs.update_resume(resume["id"], {"name": "Stolen"}) is None
    assert await theirs.delete_resume(resume["id"]) is False
    assert await theirs.update_target_job(job["id"], {"company": "Stolen"}) is None
    assert await theirs.delete_target_job(job["id"]) is False

    # And nothing changed.
    assert (await mine.list_entries())[0]["org"] == "Mine"
    assert (await mine.get_resume(resume["id"]))["name"] == "Mine"
    assert (await mine.list_target_jobs())[0]["company"] == "Acme"


async def test_deleting_a_target_unlinks_the_resume_that_pointed_at_it(store) -> None:
    mine, _ = store
    job = await mine.create_target_job({"company": "Acme", "title": "Intern"})
    resume = await mine.create_resume({"name": "SWE", "target_job_id": job["id"]})
    assert (await mine.get_resume(resume["id"]))["target_job_id"] == job["id"]

    assert await mine.delete_target_job(job["id"]) is True
    # ON DELETE SET NULL (target_job_id) — the resume survives, unlinked.
    assert (await mine.get_resume(resume["id"]))["target_job_id"] is None


async def test_target_job_link_can_be_cleared_explicitly(store) -> None:
    mine, _ = store
    job = await mine.create_target_job({"company": "Acme", "title": "Intern"})
    resume = await mine.create_resume({"name": "SWE", "target_job_id": job["id"]})

    cleared = await mine.update_resume(resume["id"], {"target_job_id": None})
    assert cleared["target_job_id"] is None


async def test_concurrent_bullet_inserts_both_survive(store) -> None:
    """Proves the row lock in _edit().

    A naive read-modify-write passes every other test in this file and fails
    this one: both writers read the same entries array and the second clobbers
    the first.
    """
    mine, _ = store
    entry = await mine.create_entry({"kind": "project", "org": "Pipeline"})

    await asyncio.gather(*(
        mine.create_bullet(entry["id"], {"text": f"Bullet {n}"}) for n in range(5)
    ))

    bullets = (await mine.list_entries())[0]["bullets"]
    assert len(bullets) == 5
    assert len({b["id"] for b in bullets}) == 5, "ids must not be reused"
    assert {b["text"] for b in bullets} == {f"Bullet {n}" for n in range(5)}


async def test_next_item_id_keeps_climbing_across_reconnects(store) -> None:
    mine, _ = store
    first = await mine.create_entry({"kind": "project", "org": "One"})
    skill = await mine.create_skill({"category": "language", "name": "Python"})
    # One shared counter across entries, bullets and skills.
    assert skill["id"] > first["id"]

    reopened = PostgresResumeStore(
        os.environ["XJOBSCORE_TEST_DSN"], account_id=mine.account_id
    )
    await reopened.connect()
    try:
        later = await reopened.create_entry({"kind": "project", "org": "Two"})
        assert later["id"] > skill["id"]
    finally:
        await reopened.close()


async def test_entries_interleave_by_position_across_kinds(store) -> None:
    """Position is allocated per kind but ordered globally."""
    mine, _ = store
    project = await mine.create_entry({"kind": "project", "org": "P1"})
    education = await mine.create_entry({"kind": "education", "org": "E1"})
    project_two = await mine.create_entry({"kind": "project", "org": "P2"})

    # P1 and E1 both take position 1; P2 takes position 2.
    ordered = [e["org"] for e in await mine.list_entries()]
    assert ordered == ["P1", "E1", "P2"]
    assert project["position"] == 1 and education["position"] == 1
    assert project_two["position"] == 2


async def test_variants_and_vocabulary_round_trip_through_jsonb(store) -> None:
    """Both new columns decode as Python lists, not as strings.

    `vocabulary` is the subtle one: it is nullable so that "never customized"
    stays distinguishable from "deliberately emptied". A fresh workspace must
    therefore report the seed list, and an explicit clear must stick.
    """
    mine, _ = store
    assert await mine.list_variants() == []
    assert len(await mine.list_vocabulary()) > 100  # the seed list

    await mine.set_variants([{"key": "Regulatory", "label": "Regulatory Affairs"}])
    assert await mine.list_variants() == [
        {"key": "regulatory", "label": "Regulatory Affairs"}
    ]

    assert await mine.set_vocabulary(["GMP", "ISO 13485"]) == ["GMP", "ISO 13485"]
    assert await mine.list_vocabulary() == ["GMP", "ISO 13485"]
    # An empty list is a real state and must survive a round trip rather than
    # decoding back into the seed.
    await mine.set_vocabulary([])
    assert await mine.list_vocabulary() == []


async def test_variants_are_per_account(store) -> None:
    mine, theirs = store
    await mine.set_variants([{"key": "biotech", "label": "BioTech"}])
    assert await theirs.list_variants() == []
    # And the audience check keys off the caller's own list, not a global one.
    entry = await theirs.create_entry({"kind": "experience", "org": "Elsewhere"})
    with pytest.raises(ValueError, match="none defined yet"):
        await theirs.create_bullet(entry["id"], {"text": "x", "audience": "biotech"})


async def _queue_application(store_, account_id, *, description="GMP required."):
    """Insert a job_posting + job_application the way the tracker would."""
    pool = store_._ready()
    # company.domain and job_posting.content_hash are both unique, so every call
    # needs its own; two tests in one session would otherwise collide.
    unique = uuid4().hex
    company_id = await pool.fetchval(
        "INSERT INTO company(name, domain) VALUES ('Medtronic', $1) RETURNING id",
        f"{_TEST_SOURCE}-{unique}.test",
    )
    # job_posting.raw_id is NOT NULL: every posting is traceable to the payload
    # it was parsed from, so the bronze row has to exist first.
    raw_id = await pool.fetchval(
        """
        INSERT INTO raw_posting(source, source_key, data, data_hash)
        VALUES ($1, $2, '{}', $2) RETURNING id
        """,
        _TEST_SOURCE,
        unique,
    )
    job_id = await pool.fetchval(
        """
        INSERT INTO job_posting(
            source, source_key, company_id, title, description, apply_url,
            content_hash, posted_at, raw_id
        ) VALUES ($1, $2, $3, 'Quality Engineer', $4,
                  'https://jobs.example.test/7', $2, '2026-08-01T00:00:00Z', $5)
        RETURNING id
        """,
        _TEST_SOURCE,
        unique,
        company_id,
        description,
        raw_id,
    )
    return await pool.fetchval(
        """
        INSERT INTO job_application(
            account_id, job_posting_id, company_snapshot, title_snapshot,
            apply_url_snapshot
        ) VALUES ($1, $2, 'Medtronic', 'Quality Engineer',
                  'https://jobs.example.test/7')
        RETURNING id
        """,
        account_id,
        job_id,
    )


async def test_a_queued_application_becomes_a_target_exactly_once(store) -> None:
    """The ON CONFLICT against a *partial* unique index is the risky SQL here.

    If the index predicate and the conflict target ever disagree, Postgres
    cannot infer the arbiter and the insert raises instead of returning the
    existing row — which would make the button fail on its second press.
    """
    mine, _ = store
    application_id = await _queue_application(mine, mine.account_id)

    target = await mine.create_target_from_application(application_id)
    assert target["company"] == "Medtronic"
    assert target["title"] == "Quality Engineer"
    assert target["jd_text"] == "GMP required."
    assert target["date_posted"] == "2026-08-01"
    assert target["job_application_id"] == application_id

    again = await mine.create_target_from_application(application_id)
    assert again["id"] == target["id"]
    assert len(await mine.list_target_jobs()) == 1


async def test_another_accounts_application_cannot_be_tailored(store) -> None:
    mine, theirs = store
    application_id = await _queue_application(mine, mine.account_id)
    # None is what the route turns into a 404. Returning a target here would
    # leak another user's company, title, and JD into this workspace.
    assert await theirs.create_target_from_application(application_id) is None
    assert await theirs.list_target_jobs() == []


async def test_deleting_an_application_keeps_the_resume_work(store) -> None:
    """ON DELETE SET NULL, not CASCADE: the tailoring outlives the queue entry."""
    mine, _ = store
    application_id = await _queue_application(mine, mine.account_id)
    target = await mine.create_target_from_application(application_id)
    await mine._ready().execute("DELETE FROM job_application WHERE id = $1", application_id)
    survivor = await mine._get_target(target["id"])
    assert survivor is not None
    assert survivor["job_application_id"] is None
    assert survivor["jd_text"] == "GMP required."
