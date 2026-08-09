"""Stage 1 -- scan the Common Crawl index for ATS tokens.

Output is one parquet per (crawl, vendor) under the working directory. A
vendor whose file already exists is skipped, which makes a run that dies four
hours in resumable rather than restartable.

Nothing here touches Postgres. The scan is the expensive, network-bound part
and it should be possible to redo the probe or the bind without paying for it
again.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from cc_ats import ccindex, vendors
from cc_ats.vendors import Vendor


def scan_sql(vendor: Vendor, files_sql: str, crawl: str) -> str:
    """One vendor's extraction, pushed entirely into the scan.

    Three filters, in the order that matters:

      1. url_surtkey range -- the only one that reduces I/O. Parquet footer
         statistics skip whole row groups because the index is sorted on this
         column.
      2. where_sql -- a cheap column predicate that cuts rows before the regex
         runs.
      3. the regex itself, then GROUP BY, so what comes back over the wire is
         thousands of tokens rather than millions of posting URLs.

    Deliberately no fetch_status filter. A token lives in the request URL and
    does not care what the response was: a 301 from a vendor's host migration
    still names its tenant, and a 404 board is a fact the probe stage is there
    to record as 'absent'. Inside subset=warc the filter was also a no-op,
    since that subset is 200s by definition.

    The STOP and TOKEN_RE checks are applied here too rather than in Python
    afterwards, so the parquet on disk is already clean and every later stage
    reads the same filtered set.
    """
    ranges = " OR ".join(
        f"(url_surtkey >= '{lo}' AND url_surtkey < '{hi}')"
        for lo, hi in (vendors.surt_range(d) for d in vendor.domains)
    )
    stop_list = ", ".join("'" + s.replace("'", "''") + "'" for s in sorted(vendors.STOP))
    return f"""
        SELECT * FROM (
            SELECT
                '{vendor.ats}'                AS ats,
                {vendors.token_sql(vendor)}   AS token,
                count(*)                      AS url_count,
                min(url)                      AS evidence_url,
                '{crawl}'                     AS crawl
            FROM read_parquet({files_sql})
            WHERE ({ranges})
              AND ({vendor.where_sql})
            GROUP BY 1, 2
        )
        WHERE token <> ''
          AND lower(split_part(token, '/', 1)) NOT IN ({stop_list})
          AND regexp_matches(token, '{vendors.TOKEN_RE.pattern}')
    """


def run(
    workdir: Path,
    crawls: int = 2,
    only: set[str] | None = None,
    memory_limit: str = "6GB",
    threads: int = 4,
) -> None:
    selected = [v for v in vendors.VENDORS if not only or v.ats in only]
    if only:
        unknown = only - {v.ats for v in vendors.VENDORS}
        if unknown:
            raise SystemExit(f"unknown vendor(s): {', '.join(sorted(unknown))}")

    con = ccindex.connect(memory_limit=memory_limit, threads=threads)
    for crawl in ccindex.latest_crawls(crawls):
        outdir = workdir / crawl
        outdir.mkdir(parents=True, exist_ok=True)

        pending = [v for v in selected if not (outdir / f"{v.ats}.parquet").exists()]
        if not pending:
            print(f"[{crawl}] all {len(selected)} vendors already scanned")
            continue

        print(f"[{crawl}] listing index files...")
        files_sql = ccindex.sql_file_list(ccindex.parquet_urls(crawl))

        for vendor in pending:
            out = outdir / f"{vendor.ats}.parquet"
            started = time.monotonic()
            try:
                con.execute(
                    f"COPY ({scan_sql(vendor, files_sql, crawl)}) "
                    f"TO '{out}' (FORMAT PARQUET)"
                )
            except duckdb.Error as exc:
                # One vendor's bad pattern should not abandon a multi-hour
                # scan; the file is simply absent and the next run retries it.
                print(f"[{crawl}] {vendor.ats}: FAILED -- {exc}")
                out.unlink(missing_ok=True)
                continue
            found = con.execute(
                f"SELECT count(*) FROM read_parquet('{out}')"
            ).fetchone()[0]
            print(
                f"[{crawl}] {vendor.ats}: {found:,} tokens "
                f"in {time.monotonic() - started:.0f}s"
            )


def load_candidates(workdir: Path, only: set[str] | None = None) -> list[dict]:
    """Every scanned token, deduped across crawls, most-linked first.

    A token seen in either crawl is a candidate; url_count sums across them and
    orders the probe queue so the boards the web actually links to get
    confirmed first.
    """
    paths = sorted(workdir.glob("*/*.parquet"))
    if not paths:
        raise SystemExit(f"no scan output under {workdir}; run `discover` first")
    files_sql = ccindex.sql_file_list([str(p) for p in paths])
    where = ""
    if only:
        wanted = ", ".join("'" + a.replace("'", "''") + "'" for a in sorted(only))
        where = f"WHERE ats IN ({wanted})"
    con = duckdb.connect()
    rows = con.execute(
        f"""
        SELECT ats, token,
               sum(url_count)     AS url_count,
               min(evidence_url)  AS evidence_url,
               max(crawl)         AS crawl
        FROM read_parquet({files_sql})
        {where}
        GROUP BY 1, 2
        ORDER BY url_count DESC
        """
    ).fetchall()
    return [
        {"ats": r[0], "token": r[1], "url_count": int(r[2]), "evidence_url": r[3], "crawl": r[4]}
        for r in rows
    ]
