"""Initialize a local frontend database and seed stable IDs from existing Neon.

Run once before publishing: python -m xjobscore.frontend_setup.
DATABASE_URL is the pipeline; PUBLISH_DATABASE_URL is Neon;
FRONTEND_DATABASE_URL is the empty local serving database.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from xjobscore import publish, store


async def initialize(source, neon, local):
    if await local.fetchval('SELECT count(*) FROM job_posting'):
        raise RuntimeError('Local frontend already contains postings; refusing to initialize it again')
    for table in (
        'job_application','job_application_event','job_feedback','resume_workspace','resume_target','resume_document'
    ):
        if await local.fetchval('SELECT count(*) FROM '+table):
            raise RuntimeError('Local frontend contains personal data; refusing to overwrite it')
    companies = await neon.fetch('SELECT '+', '.join(publish.COMPANY_COLUMNS)+' FROM company')
    jobs = await neon.fetch('SELECT '+', '.join(publish.JOB_COLUMNS)+', dup_group_id FROM job_posting')
    logos = await neon.fetch('SELECT company_id, bytes FROM company_logo')
    groups = await neon.fetch('SELECT id, primary_posting_id, state, evidence FROM job_posting_dup_group')
    coverage = await neon.fetchrow('SELECT id,payload,computed_at FROM coverage_snapshot WHERE id')
    home_companies = await source.fetch('SELECT id,name,domain FROM company')
    target_names = Counter(c['name'] for c in companies if c['domain'] is None)
    home_names = Counter(c['name'] for c in home_companies if c['domain'] is None)
    home_by_name = {c['name']:c for c in home_companies if c['domain'] is None and home_names[c['name']]==1}
    mapping = []
    seen = {}
    for c in companies:
        if c['domain'] is not None:
            key = ['domain',c['domain'].lower()]
        elif target_names[c['name']]==1 and c['name'] in home_by_name:
            home = home_by_name[c['name']]
            key = ['source_id',home['id'],home['name']]
        else:
            key = ['legacy_neon_company',c['id']]
        identity = ('company',json.dumps(key,ensure_ascii=False))
        if identity in seen and seen[identity]!=c['id']:
            raise RuntimeError('Ambiguous Neon company identity; resolve it before publishing')
        seen[identity] = c['id']
        mapping.append(('company',key,c['id']))
    mapping.extend(('job',[j['source'],j['source_key']],j['id']) for j in jobs)
    # Seed the same IDs into the local frontend without copying any personal data.
    async with local.transaction():
        if companies:
            await local.executemany(publish.UPSERT_COMPANY_SQL,[publish._values(c,publish.COMPANY_COLUMNS) for c in companies])
        if logos:
            await local.executemany(publish.UPSERT_LOGO_SQL,[(r['company_id'],r['bytes']) for r in logos])
        if jobs:
            await local.executemany(publish.UPSERT_JOB_SQL,[publish._values(j,publish.JOB_COLUMNS) for j in jobs])
        if groups:
            await local.executemany('INSERT INTO job_posting_dup_group(id,primary_posting_id,state,evidence) VALUES($1,$2,$3,$4)',[tuple(r.values()) for r in groups])
            await local.executemany('UPDATE job_posting SET dup_group_id=$2 WHERE id=$1',[(j['id'],j['dup_group_id']) for j in jobs if j['dup_group_id'] is not None])
        if coverage:
            await local.execute(publish.UPSERT_COVERAGE_SQL,coverage['id'],coverage['payload'],coverage['computed_at'])
        async with source.transaction():
            await source.execute('SELECT pg_advisory_xact_lock(627031007)')
            if await source.fetchval("SELECT EXISTS(SELECT 1 FROM frontend_identity WHERE kind='meta' AND key='[\"initialized\"]')"):
                raise RuntimeError('Publication identities already initialized; refusing to replace them')
            await source.executemany('INSERT INTO frontend_identity(kind,key,id) VALUES($1,$2,$3)',mapping)
            await source.execute("SELECT setval(pg_get_serial_sequence('frontend_identity','id'),greatest(coalesce((SELECT max(id) FROM frontend_identity),1),1),true)")
            await source.execute("INSERT INTO frontend_identity(kind,key,id) VALUES('meta','[\"initialized\"]',0)")
    return {'seeded_jobs':len(jobs),'seeded_companies':len(companies),'copied_personal_records':0}


async def main():
    publish._load_dotenv()
    source_dsn = os.environ['DATABASE_URL']
    neon_dsn = os.environ['PUBLISH_DATABASE_URL']
    local_dsn = os.environ['FRONTEND_DATABASE_URL']
    if urlsplit(local_dsn).hostname not in (None,'localhost','127.0.0.1','::1'):
        raise RuntimeError('FRONTEND_DATABASE_URL must identify a local database')
    if local_dsn == source_dsn:
        raise RuntimeError('Frontend and pipeline must use different databases')
    pool = await store.connect(source_dsn)
    neon = await publish.connect_target(neon_dsn,server_settings={'default_transaction_read_only':'on'})
    local = await publish.connect_target(local_dsn)
    try:
        async with pool.acquire() as source:
            print(json.dumps(await initialize(source,neon,local)))
    finally:
        await local.close()
        await neon.close()
        await pool.close()


if __name__ == '__main__':
    asyncio.run(main())
