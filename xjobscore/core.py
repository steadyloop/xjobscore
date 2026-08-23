"""Orchestrator: fetch an empty DB, then run silver and gold on everything.

Personal entrypoint, kept separate from run.py. Extend this file directly.
"""

from __future__ import annotations

import asyncio

from xjobscore import run, store
from xjobscore.net import Net


async def main() -> None:
    cfg = run.load_config([])
    pool = await store.connect(cfg.dsn)
    try:
        async with pool.acquire() as conn:
            raw_postings = await conn.fetchval("SELECT count(*) FROM raw_posting")

        if raw_postings == 0:
            print("DB is empty, fetching all sources...")
            async with Net(user_agent=cfg.user_agent) as net:
                result = await run.fetch_all(pool, net, cfg)
            print(f"fetched: {result}")

        print("running silver...")
        silver_results = await run.parse_all(pool, cfg)
        print(f"silver: {len(silver_results)} feeds replayed")

        print("running gold...")
        gold_results = await run.run_enrichments(pool, cfg)
        print(f"gold: {gold_results}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
