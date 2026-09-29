import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from xjobscore import coverage_snapshot
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
                source_url, apply_url, content_hash, raw_id, job_type, remote,
                attributes, locations
            ) VALUES (
                $1, 'job-1', $2, 'Summer Platform Intern', 'Build Python services',
                'Remote, United States', 'https://repository.test/job',
                'https://repository.test/apply', 'content-hash', $3,
                'intern', 'yes', $4::jsonb, $5::jsonb
            ) RETURNING id
            """,
            posting_source,
            company_id,
            raw_id,
            '{"version":1,"term":"Summer 2027","skills":["python","postgresql"],"summary":"Build Python services with the platform engineering team."}',
            '[{"country":"US","state":null,"city":null}]',
        )
        await pool.execute(
            "INSERT INTO token(ats, token, company_id, status) VALUES ($1, 'board', $2, 'live')",
            source,
            company_id,
        )
        feed_id = await pool.fetchval(
            "INSERT INTO feed(source, key) VALUES ($1, 'board') RETURNING id", source
        )

        jobs = await repository.list_jobs(job_types=["intern"])
        assert jobs["total"] == 1
        assert jobs["jobs"][0]["source"] == posting_source
        assert jobs["jobs"][0]["source_url"] == "https://repository.test/job"
        assert jobs["jobs"][0]["summary"] == (
            "Build Python services with the platform engineering team."
        )
        assert "description" not in jobs["jobs"][0]
        assert jobs["jobs"][0]["remote"] == "yes"
        assert jobs["jobs"][0]["location"] == "United States"
        assert jobs["jobs"][0]["locations"] == [
            {"country": "US", "state": None, "city": None}
        ]
        application = await repository.create_application(job_id)
        assert application is not None
        assert application["stage"] == "considering"
        assert application["location"] == "United States"
        assert await pool.fetchval(
            "SELECT account_id FROM job_application WHERE id = $1", application["id"]
        ) == LOCAL_ACCOUNT_ID

        applied = await repository.update_application(
            application["id"], {"stage": "applied"}
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
        await coverage_snapshot.refresh(pool)
        assert (await repository.source_coverage())["source_count"] == 1

        # The fixture feed is polling=false, so it is not part of the fleet map.
        assert source not in {p["source"] for p in (await repository.board_map())["providers"]}
        await pool.execute("UPDATE feed SET polling = true WHERE id = $1", feed_id)
        await coverage_snapshot.refresh(pool)
        board_map = await repository.board_map()
        provider = next(p for p in board_map["providers"] if p["source"] == source)
        assert provider["board_count"] == 1
        assert provider["err_count"] == 0
        board = provider["boards"][0]
        assert board["key"] == "board"
        assert board["company"] == "Repository Test"  # from token.company_id -> company.name
        assert board["state"] == "never"  # last_poll_at is NULL
        assert board["coverage"] == 1
        assert board_map["totals"]["boards"] >= 1
    finally:
        await pool.execute(
            "DELETE FROM job_application WHERE account_id = $1 AND (job_posting_id = $2 OR company_snapshot IN ('Repository Test', 'External Co'))",
            LOCAL_ACCOUNT_ID,
            locals().get("job_id", -1),
        )
        await pool.execute("DELETE FROM feed WHERE source = $1", source)
        await pool.execute("DELETE FROM token WHERE ats = $1 AND token = 'board'", source)
        await pool.execute("DELETE FROM job_posting WHERE source = $1", posting_source)
        await pool.execute("DELETE FROM raw_posting WHERE source = $1", posting_source)
        await pool.execute("DELETE FROM company WHERE domain = 'repository.test'")
        await coverage_snapshot.refresh(pool)
        await repository.close()


@pytest.mark.asyncio
async def test_duplicate_groups_are_one_trackable_opportunity_with_active_fallback() -> None:
    repository = PostgresRepository(os.environ["XJOBSCORE_TEST_DSN"])
    await repository.connect()
    pool = repository._ready()
    source = f"oneshot_dup_repository_test_{uuid4().hex}"
    company_domain = f"{source}.test"
    group_id, pending_id, rejected_id = uuid4(), uuid4(), uuid4()
    job_ids: list[int] = []
    try:
        company_id = await pool.fetchval(
            "INSERT INTO company(name, domain) VALUES ($1, $2) RETURNING id",
            "Duplicate Repository Test",
            company_domain,
        )
        raw_id = await pool.fetchval(
            """
            INSERT INTO raw_posting(source, source_key, data, data_hash)
            VALUES ($1, 'raw', '{}', 'dup-raw-hash') RETURNING id
            """,
            source,
        )

        async def posting(key: str, provider: str, posted_at: str) -> int:
            job_id = await pool.fetchval(
                """
                INSERT INTO job_posting(
                    source, source_key, company_id, title, source_url, apply_url,
                    posted_at, content_hash, raw_id, job_type, attributes, locations
                ) VALUES (
                    $1, $2, $3, 'Duplicate-aware Intern', $4, $5, $6,
                    $7, $8, 'intern', '{"version":1}', '[]'
                ) RETURNING id
                """,
                f"{provider}:duplicate-test",
                key,
                company_id,
                f"https://{provider}.example.test/jobs/{key}",
                f"https://{provider}.example.test/apply/{key}",
                datetime.fromisoformat(posted_at.replace("Z", "+00:00")),
                f"hash-{key}",
                raw_id,
            )
            job_ids.append(job_id)
            return job_id

        primary = await posting("primary", "greenhouse", "2026-08-20T00:00:00Z")
        alternate = await posting("alternate", "workday", "2026-08-19T00:00:00Z")
        closed_alternate = await posting("closed", "lever", "2026-08-18T00:00:00Z")
        pending_a = await posting("pending-a", "ashby", "2026-08-17T00:00:00Z")
        pending_b = await posting("pending-b", "icims", "2026-08-16T00:00:00Z")
        rejected_a = await posting("rejected-a", "greenhouse", "2026-08-15T00:00:00Z")
        rejected_b = await posting("rejected-b", "workday", "2026-08-14T00:00:00Z")

        await pool.executemany(
            """
            INSERT INTO job_posting_dup_group(id, primary_posting_id, state, evidence)
            VALUES ($1, $2, $3, '{}')
            """,
            [
                (group_id, primary, "pending"),
                (pending_id, None, "pending"),
                (rejected_id, rejected_a, "rejected"),
            ],
        )
        await pool.execute(
            "UPDATE job_posting SET dup_group_id = $1 WHERE id = ANY($2::bigint[])",
            group_id,
            [primary, alternate, closed_alternate],
        )
        await pool.execute(
            "UPDATE job_posting SET dup_group_id = $1 WHERE id = ANY($2::bigint[])",
            pending_id,
            [pending_a, pending_b],
        )
        await pool.execute(
            "UPDATE job_posting SET dup_group_id = $1 WHERE id = ANY($2::bigint[])",
            rejected_id,
            [rejected_a, rejected_b],
        )
        await pool.execute(
            "UPDATE job_posting SET status = 'closed' WHERE id = $1",
            closed_alternate,
        )

        jobs = await repository.list_jobs(
            job_types=["intern"], q="Duplicate Repository Test"
        )
        assert jobs["total"] == 5
        assert jobs["job_type_counts"]["intern"] == 5
        assert [row["id"] for row in jobs["jobs"]].count(primary) == 1
        primary_row = next(row for row in jobs["jobs"] if row["id"] == primary)
        assert len(primary_row["other_sources"]) == 1
        other = primary_row["other_sources"][0]
        assert other["source"] == "workday:duplicate-test"
        assert other["url"] == "https://workday.example.test/apply/alternate"
        assert datetime.fromisoformat(other["posted_at"]).astimezone(timezone.utc) == datetime(
            2026, 8, 19, tzinfo=timezone.utc
        )
        assert datetime.fromisoformat(other["first_seen_at"])
        assert not ({"dup_group_id", "group_state"} & primary_row.keys())

        guest_hidden = await repository.list_jobs(
            job_types=["intern"],
            q="Duplicate Repository Test",
            exclude_job_ids=[alternate],
            hide_tracked=False,
        )
        assert guest_hidden["total"] == 4
        assert primary not in {row["id"] for row in guest_hidden["jobs"]}

        application = await repository.create_application(primary)
        assert application is not None
        duplicate = await repository.create_application(alternate)
        assert duplicate is not None
        assert duplicate["id"] == application["id"]
        assert duplicate["already_exists"] is True
        tracked_hidden = await repository.list_jobs(
            job_types=["intern"], q="Duplicate Repository Test"
        )
        assert tracked_hidden["total"] == 4

        # Skipping one member hides the whole group, like tracking does, and
        # restoring it brings the group back.
        skipped = await repository.set_feedback(pending_a, kind="skip")
        assert skipped is not None and skipped["kind"] == "skip"
        assert (await repository.list_jobs(
            job_types=["intern"], q="Duplicate Repository Test"
        ))["total"] == 3
        reasoned = await repository.set_feedback(
            pending_a, kind="skip", skip_reason="location"
        )
        assert reasoned["skip_reason"] == "location"
        assert reasoned["created_at"] == skipped["created_at"]
        hidden = await repository.list_feedback()
        assert [row["job_id"] for row in hidden["hidden"]][:1] == [pending_a]
        assert hidden["hidden"][0]["job"]["title"] == "Duplicate-aware Intern"
        assert await repository.delete_feedback(pending_a) is True
        assert await repository.delete_feedback(pending_a) is False
        assert (await repository.list_jobs(
            job_types=["intern"], q="Duplicate Repository Test"
        ))["total"] == 4
        assert await repository.set_feedback(-1, kind="skip") is None

        await pool.execute("UPDATE job_posting SET status = 'closed' WHERE id = $1", primary)
        fallback = await repository.list_jobs(
            job_types=["intern"],
            q="Duplicate Repository Test",
            hide_tracked=False,
        )
        assert fallback["total"] == 5
        fallback_row = next(row for row in fallback["jobs"] if row["id"] == alternate)
        assert fallback_row["other_sources"] == []
    finally:
        if job_ids:
            await pool.execute(
                "DELETE FROM job_application WHERE account_id = $1 AND job_posting_id = ANY($2::bigint[])",
                LOCAL_ACCOUNT_ID,
                job_ids,
            )
            await pool.execute(
                "DELETE FROM job_feedback WHERE job_posting_id = ANY($1::bigint[])",
                job_ids,
            )
            await pool.execute(
                "UPDATE job_posting SET dup_group_id = NULL WHERE id = ANY($1::bigint[])",
                job_ids,
            )
        await pool.execute(
            "DELETE FROM job_posting_dup_group WHERE id = ANY($1::uuid[])",
            [group_id, pending_id, rejected_id],
        )
        if job_ids:
            await pool.execute("DELETE FROM job_posting WHERE id = ANY($1::bigint[])", job_ids)
        await pool.execute("DELETE FROM raw_posting WHERE source = $1", source)
        await pool.execute("DELETE FROM company WHERE domain = $1", company_domain)
        await repository.close()
