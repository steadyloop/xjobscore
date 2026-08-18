import os

import pytest

from oneshot_ui.repository import LOCAL_ACCOUNT_ID, PostgresRepository


pytestmark = pytest.mark.skipif(
    not os.environ.get("XJOBSCORE_TEST_DSN"),
    reason="set XJOBSCORE_TEST_DSN to run PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_schema_sql_repository_round_trip() -> None:
    repository = PostgresRepository(os.environ["XJOBSCORE_TEST_DSN"])
    await repository.connect()
    pool = repository._ready()
    source = "oneshot_repository_test"
    posting_source = f"{source}:board"
    try:
        company_id = await pool.fetchval(
            "INSERT INTO company(name, domain) VALUES ('Repository Test', 'repository.test') RETURNING id"
        )
        raw_id = await pool.fetchval(
            """
            INSERT INTO raw_posting(source, source_key, data, data_hash)
            VALUES ($1, 'raw-1', '{}', 'raw-hash') RETURNING id
            """,
            posting_source,
        )
        job_id = await pool.fetchval(
            """
            INSERT INTO job_posting(
                source, source_key, company_id, title, description, location_text,
                source_url, apply_url, content_hash, raw_id, attributes
            ) VALUES (
                $1, 'job-1', $2, 'Summer Platform Intern', 'Build Python services',
                'Remote, United States', 'https://repository.test/job',
                'https://repository.test/apply', 'content-hash', $3,
                $4::jsonb
            ) RETURNING id
            """,
            posting_source,
            company_id,
            raw_id,
            '{"version":1,"opportunity_type":"internship","term":"Summer 2027","workplace":"remote","skills":["Python","PostgreSQL"]}',
        )
        feed_id = await pool.fetchval(
            "INSERT INTO feed(source, key) VALUES ($1, 'board') RETURNING id", source
        )
        await pool.execute("INSERT INTO schedule(feed_id) VALUES ($1)", feed_id)

        jobs = await repository.list_jobs(opportunity_types=["internship"])
        assert jobs["total"] == 1
        assert jobs["jobs"][0]["source"] == posting_source
        assert jobs["jobs"][0]["source_url"] == "https://repository.test/job"
        assert jobs["jobs"][0]["attributes"]["workplace"] == "remote"
        application = await repository.create_application(job_id)
        assert application is not None
        assert application["stage"] == "considering"
        assert await pool.fetchval(
            "SELECT account_id FROM job_application WHERE id = $1", application["id"]
        ) == LOCAL_ACCOUNT_ID

        applied = await repository.update_application(
            application["id"], {"stage": "applied", "confirmed": True}
        )
        assert applied is not None
        assert applied["submitted_at"] is not None
        assert applied["events"][0]["to_stage"] == "applied"

        external = await repository.create_external_application(
            company="External Co",
            title="Engineer",
            url="https://external.test/job",
        )
        assert external["external"] is True
        assert await repository.delete_application(external["id"])
        assert (await repository.source_coverage())["source_count"] == 1
    finally:
        await pool.execute(
            "DELETE FROM job_application WHERE account_id = $1 AND (job_posting_id = $2 OR company_snapshot IN ('Repository Test', 'External Co'))",
            LOCAL_ACCOUNT_ID,
            locals().get("job_id", -1),
        )
        await pool.execute("DELETE FROM feed WHERE source = $1", source)
        await pool.execute("DELETE FROM job_posting WHERE source = $1", posting_source)
        await pool.execute("DELETE FROM raw_posting WHERE source = $1", posting_source)
        await pool.execute("DELETE FROM company WHERE domain = 'repository.test'")
        await repository.close()
