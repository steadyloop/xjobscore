"""Run corrected adapter locations through storage and the real serving SQL.

Only the existing per-test throwaway schema is written. Production postings,
feeds and accounts are never used by this test.
"""
from datetime import datetime, timezone

from xjobscore import publish, silver, sources, store
from xjobscore.records import Feed, Item


async def test_dutch_correction_preserves_us_and_unknown_serving_eligibility(core_schema):
    cases = [
        ('dutch-amsterdam', 'Amsterdam, Noord-Holland, Nederland', []),
        ('dutch-rijssen', 'Rijssen, Overijssel, Nederland', []),
        ('dutch-unknown-city', 'Koudekerk aan den Rijn, Zuid-Holland, Nederland', []),
        ('us-nederland-tx', 'Nederland, TX, United States', []),
        ('us-nederland-co', 'Nederland, CO', []),
        ('unknown', None, []),
        ('ambiguous-nederland', 'Nederland', []),
        ('mixed-text', 'Amsterdam, Noord-Holland, Nederland; Austin, TX', []),
        ('mixed-city-only', 'Seattle, Amsterdam, Noord-Holland, Nederland', []),
        ('mixed-addresses', 'Rijssen, Overijssel, Nederland', [
            {'city': 'Monee', 'state': 'Illinois', 'country_code': 'US'},
            {'city': 'Fresno', 'state': 'California', 'country_code': 'US'},
        ]),
        ('mixed-country-only', 'Amsterdam, Noord-Holland, Nederland', [{'country_code': 'US'}]),
    ]
    async with core_schema.pool.acquire() as conn:
        company_id = await conn.fetchval("INSERT INTO company(name) VALUES('Location regression') RETURNING id")
        for key, location, addresses in cases:
            posting = sources.json_api.parse(
                'recruitee', Feed(id=1, source='recruitee', key='regression'),
                Item(key, {'title': 'Software Intern', 'location': location,
                           'locations': addresses, 'careers_url': 'https://example.com/'+key}), {},
            )[0]
            values = silver._projection(posting)
            raw_id = await conn.fetchval(
                "INSERT INTO raw_posting(source, source_key, data, data_hash) VALUES($1,$2,$3,$4) RETURNING id",
                'recruitee:regression', key, {'version': 1}, key,
            )
            await store.upsert_job_posting(
                conn, source='recruitee:regression', source_key=key, company_id=company_id,
                values=values, content_hash=silver._content_hash(values), raw_id=raw_id,
                seen_at=datetime.now(timezone.utc),
            )
        cohort = await conn.fetch(publish.COHORT_SQL)
        selected = await conn.fetch(
            "SELECT source_key FROM job_posting WHERE id=ANY($1::bigint[])",
            [row['id'] for row in cohort],
        )
        assert {row['source_key'] for row in selected} == {
            'us-nederland-tx', 'us-nederland-co', 'unknown', 'ambiguous-nederland',
            'mixed-text', 'mixed-addresses', 'mixed-country-only',
            'mixed-city-only',
        }
        stored = await conn.fetchval("SELECT locations FROM job_posting WHERE source_key='mixed-addresses'")
        assert {'country':'US','state':'IL','city':'Monee'} in stored
        assert {'country':'US','state':'CA','city':'Fresno'} in stored
        assert not any(p['country']=='US' and p['city']=='Nederland' for p in stored)
