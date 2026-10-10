"""The research archive is idempotent and survives source edits and deletion."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re


async def test_research_archive_retains_history_and_supports_queries(core_schema):
    schema = core_schema.schema + "_analysis"
    sql = (Path(__file__).resolve().parents[2] / "analysis.sql").read_text()
    sql = re.sub(r"\banalysis\b", schema, sql).replace("public.", core_schema.schema + ".")
    closed = datetime.now(timezone.utc) - timedelta(days=1)
    async with core_schema.pool.acquire() as conn:
        try:
            company = await conn.fetchval("INSERT INTO company(name) VALUES('Test company') RETURNING id")
            for key, title, status, signal in (("closed", "Senior Software Engineer", "closed", closed),
                                               ("active", "Data Scientist", "active", None),
                                               ("undated", "Research Scientist", "closed", None)):
                raw = await conn.fetchval("""INSERT INTO raw_posting(source,source_key,data,data_hash)
                    VALUES('lever:test',$1,'{}',$1) RETURNING id""", key)
                await conn.execute("""INSERT INTO job_posting(source,source_key,company_id,title,status,
                    content_hash,raw_id,first_seen_at,last_seen_at,paged_closed_at)
                    VALUES('lever:test',$1,$2,$3,$4,$1,$5,$6,$6,$7)""",
                    key, company, title, status, raw, closed - timedelta(days=1), signal)
            await conn.execute(sql)
            await conn.execute(sql)
            row = await conn.fetchrow(f"SELECT * FROM {schema}.closed_job")
            assert await conn.fetchval(f"SELECT count(*) FROM {schema}.closed_job") == 1
            assert row['title'] == 'Senior Software Engineer'
            assert row['role_family'] == 'Software engineering'
            assert row['closed_at'] == closed
            assert await conn.fetchval(f"SELECT sum(closures) FROM {schema}.closed_job_daily") == 1
            assert await conn.fetchval(f"SELECT sum(closures) FROM {schema}.closed_job_hourly") == 1
            for title, family in [('Machine Learning Engineer', 'Data & AI'),
                                  ('Accountant', 'Finance & accounting'), ('Account Executive', 'Sales & customer'),
                                  ('Registered Nurse', 'Healthcare & clinical'), ('Unknown position', 'Other roles')]:
                assert await conn.fetchval(f"SELECT {schema}.role_family($1)", title) == family
            await conn.execute("""UPDATE job_posting SET title='Changed after closure',paged_closed_at=$1
                WHERE source_key='closed'""", closed - timedelta(hours=1))
            await conn.fetchval(f"SELECT {schema}.refresh_closed_jobs()")
            row = await conn.fetchrow(f"SELECT * FROM {schema}.closed_job")
            assert row['title'] == 'Senior Software Engineer'
            assert row['closed_at'] == closed - timedelta(hours=1)
            await conn.execute("DELETE FROM job_posting WHERE source_key='closed'")
            await conn.fetchval(f"SELECT {schema}.refresh_closed_jobs()")
            assert await conn.fetchval(f"SELECT count(*) FROM {schema}.closed_job") == 1
        finally:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
