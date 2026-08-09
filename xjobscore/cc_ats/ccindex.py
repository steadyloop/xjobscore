"""Reading the Common Crawl columnar index from a laptop, over plain HTTPS.

No AWS account, no Athena. Two things are worth knowing before changing this:

  - DuckDB cannot glob over HTTPS, so the parquet file list has to come from
    somewhere. It comes from the per-crawl paths manifest that Common Crawl
    publishes; anonymous S3 ListObjectsV2 against the bucket returns 403, so
    the obvious approach does not work.

  - Footer reads are per-file and are the fixed cost of a session. DuckDB
    caches them, so every vendor query after the first is much cheaper. Run
    all vendors in one process; never fork per vendor.
"""

from __future__ import annotations

import gzip

import duckdb
import httpx

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
CDN = "https://data.commoncrawl.org/"
PATHS = CDN + "crawl-data/{crawl}/cc-index-table.paths.gz"

USER_AGENT = "discovery/1.0"


def latest_crawls(n: int = 2) -> list[str]:
    """Crawl ids, newest first.

    Resolved at runtime rather than pinned: crawl ids are minted roughly
    monthly and a hardcoded one silently goes stale into an empty scan.
    """
    response = httpx.get(COLLINFO, timeout=60, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return [c["id"] for c in response.json()[:n]]


# `warc` is successfully fetched pages; `crawldiagnostics` is redirects and
# errors. Both are scanned, because a token lives in the *request* URL and does
# not care what the response was. Measured on CC-MAIN-2026-30, crawldiagnostics
# adds 29% more Greenhouse tenants -- almost all of it 301/302 from Greenhouse's
# boards -> job-boards host migration, which would otherwise be invisible.
# `robotstxt` is the remaining subset and genuinely holds no tenants.
SUBSETS = ("warc", "crawldiagnostics")


def parquet_urls(crawl: str, subsets: tuple[str, ...] = SUBSETS) -> list[str]:
    """Every parquet file in the requested subsets of one crawl, as CDN URLs."""
    response = httpx.get(
        PATHS.format(crawl=crawl), timeout=120, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    keys = gzip.decompress(response.content).decode().splitlines()
    wanted = tuple(f"/subset={s}/" for s in subsets)
    urls = [CDN + k for k in keys if k.endswith(".parquet") and any(w in k for w in wanted)]
    if not urls:
        raise RuntimeError(f"no parquet files listed for {crawl} in subsets {subsets}")
    return urls


def connect(memory_limit: str = "6GB", threads: int = 4) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Best-effort tuning. These pragma names have drifted across DuckDB
    # versions and none of them is load-bearing, so a missing one should not
    # fail a scan that takes hours.
    for pragma in (
        f"SET memory_limit='{memory_limit}'",
        f"SET threads={threads}",
        "SET preserve_insertion_order=false",
        "SET enable_http_metadata_cache=true",
        "SET http_retries=10",
        "SET http_retry_wait_ms=500",
        "SET http_timeout=120000",
        "SET enable_progress_bar=true",
    ):
        try:
            con.execute(pragma)
        except duckdb.Error:
            pass
    return con


def sql_file_list(urls: list[str]) -> str:
    return "[" + ", ".join("'" + u + "'" for u in urls) + "]"
