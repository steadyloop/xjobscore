# Future plans

## Bounded parallel Common Crawl scans

Keyscan currently scans Common Crawl sequentially: each crawl snapshot is
processed one ATS rule at a time. This is a deliberate safe default, but it
leaves wall-clock time on the table.

Do not run all 37 ATS scans concurrently. Every scan reads the same large set
of remote Common Crawl Parquet files, so unbounded concurrency would multiply
HTTP range and metadata requests, DuckDB workers, memory use, and failure
pressure on the Common Crawl CDN. It would also make terminal progress,
checkpointing, and candidate persistence harder to reason about.

Consider a bounded worker pool of two to four ATS scans per crawl snapshot:

```text
ATS rules -> bounded queue (2-4 workers) -> per-vendor Parquet artifact
```

Requirements before implementing it:

- Preserve the existing per-`(crawl, source)` checkpoint and resumability.
- Keep one durable timing/count/error log event per completed vendor scan.
- Avoid concurrent writes to the same SQLite connection; collect worker
  results and persist candidates in a controlled path, or use short isolated
  transactions.
- Set an explicit DuckDB thread budget per worker to avoid CPU oversubscription.
- Bound concurrent remote requests and test against Common Crawl failures and
  throttling.
- Make console progress concurrency-safe; structured JSONL logs remain the
  source of truth.

The current implementation opens a fresh DuckDB connection for each vendor,
so it does not reuse DuckDB HTTP metadata caching across scans. Benchmark a
small pool against a shared-connection sequential implementation before
choosing a default: fewer connections may provide much of the benefit without
additional CDN load.
