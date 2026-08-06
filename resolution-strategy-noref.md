# Job resolution strategy

The key design decision is: do not make the unified board row your unit of ingestion.

A unified role should be a reversible grouping of source postings. That gives you deduplication without deleting source evidence, accurate disappearance tracking, and the ability to correct a bad merge later.

## Recommended data model

| Entity | Stable identity | Purpose |
|---|---|---|
| `feed` | source + board/tenant | One pollable job board |
| `poll_run` | generated UUID | Records completeness, counts, errors, timing |
| `raw_artifact` | content hash | Immutable list/detail response |
| `source_posting` | `(feed_id, external_id)` | One role as represented by one source |
| `posting_version` | source posting + content hash | Historical changes to that posting |
| `unified_role` | internal UUID | User-facing deduplicated role |
| `role_membership` | source posting → unified role | Reversible merge with evidence/confidence |
| `application` | internal UUID | User pipeline item tied to an immutable posting snapshot |

An application should reference the exact source posting/version used to apply, with the unified role only as a secondary current association. Otherwise, correcting a deduplication merge could corrupt application history.

## Detecting vanished roles

Treat “missing from the board” and “confirmed closed” as different facts.

For every poll, record:

- `coverage_status`: `complete`, `partial`, `not_modified`, `anomalous`, or `error`
- expected count, fetched count, unique external-ID count
- list membership observed during the run
- detail-hydration status separately
- ETag/Last-Modified and request metadata
- parsing/anomaly counts

After a genuinely complete authoritative ATS poll:

1. Mark every returned source posting `last_seen_at=now`.
2. Any previously listed posting not returned becomes `missing_from_feed`.
3. Record `first_missing_at` and disappearance evidence.
4. Optionally confirm closure through a detail URL, explicit source flag, 404/410, or a second complete poll.
5. Never delete it; allow it to reopen if it reappears.

Do not reconcile absence after:

- a pagination or detail failure;
- reaching a safety page cap before the reported total;
- a suspicious zero-result response;
- an authentication/challenge page;
- parsing fewer records than the source reported;
- a 304 response.

Closure by absence must only follow a complete run. Completeness needs to be a property proven by each poll, not just a static feed flag.

For list-plus-detail sources, separate board coverage from detail hydration. Once all list pages are fetched, you know which IDs vanished even if one description request fails. A single `truncated` value should not conflate those two operations.

## Cross-source deduplication

Use a layered identity process, not one fuzzy score.

### 1. Deterministic matches

Automatically merge when you have strong evidence:

- Same normalized origin ATS URL.
- Same ATS family, tenant/board, and ATS external ID.
- An aggregator link resolves to an already-known origin posting.
- Same resolved employer and credible requisition ID.
- Vendor-specific URL parsing extracts the same ATS identity.

URL normalization should remove tracking parameters and unwrap redirect URLs, but preserve parameters that are part of an ATS identity.

### 2. Candidate generation

Only compare plausible pairs, usually within the same resolved employer:

- normalized title;
- requisition ID;
- location sets;
- employment/workplace type;
- publish-date proximity;
- description fingerprint after removing employer boilerplate;
- normalized origin domain and ATS family.

Embeddings are useful for finding candidates, but should not be the deciding identity signal.

### 3. Explainable match decisions

Assign must-link, cannot-link, and weighted evidence:

- High confidence: merge automatically.
- Medium confidence: review queue.
- Low confidence: leave separate.

Store the match evidence, algorithm version, confidence, and human overrides. Do not let ordinary transitive clustering turn `A≈B` and `B≈C` into an unexamined `A=C` merge.

Title plus employer is not sufficient: employers routinely publish multiple “Software Engineer” roles, location variants, evergreen roles, and multiple openings under similar descriptions.

### 4. Field-level canonicalization

Do not overwrite several source rows with one winner. Construct the unified display record using field provenance:

- Origin ATS beats secondhand aggregators.
- Fresh origin data beats stale origin data.
- Union compatible location arrays.
- Retain conflicting values and their source.
- A stale secondhand “open” flag must not override a fresh origin closure.

The unified role is active when credible, fresh origin evidence says it is active—not merely when any duplicate says “open.”

## Should ingestion preserve all source fields?

Yes—at the raw ingestion layer.

But no—you should not promote every source field into canonical columns or manually copy all of them into `source_extra`.

Preserve:

- exact raw list response;
- exact raw detail response;
- response-root metadata;
- each raw occurrence and its position/path in the response;
- request URL/parameters with secrets removed;
- retrieval time, status, validators, parser version, and content hash.

Then derive a compact canonical representation for querying.

This gives you:

- parser replay without refetching closed jobs;
- access to future-use fields you did not initially understand;
- schema-drift diagnosis;
- deduplication improvements using historical fields;
- auditability of closure and merge decisions.

Store volatile fields too, but exclude them from identity/change hashes. Compress and content-address raw artifacts so unchanged payloads are stored once. A 304 needs only a new poll observation referencing the previous artifact.

For list-plus-detail APIs, do not flatten with `{**summary, **detail}` because colliding field names destroy one value. Preserve a composite such as:

```text
{
  list_summary: ...,
  detail: ...,
  list_context: ...
}
```

or retain separate artifacts joined to the same source posting.

Public posting data and application/user data should also have different retention and access policies.

## Review of the source adapters and fixtures

The intended bronze/silver split is sound. Raw items are intended to lose no fields, while canonical parsing happens later. That is the correct direction.

There are, however, several important gaps.

### 1. List-plus-detail sources currently lose data

These adapters discard their list summaries after fetching detail:

- BambooHR
- SmartRecruiters
- Rippling
- Workday

The fixtures demonstrate why this matters. SmartRecruiters list and detail `customField` values differ, while Workday list items carry `externalPath`, `locationsText`, `postedOn`, and `bulletFields` that are not preserved as list evidence.

Rippling also drops duplicate list occurrences before storage. Even if detail currently returns aggregated `workLocations`, every raw list occurrence should be retained before source-level deduplication.

Oracle tries to preserve both responses, but flattening summary and detail dictionaries destructively overwrites same-named fields.

### 2. The config-driven in-house adapter is unsafe for closure today

The adapter concept is good, but its current implementation should not be considered a reliable complete-board fetcher:

- Configured base query parameters are not applied.
- Configured pagination parameter names are ignored; the implementation always emits `page` or `offset`. Microsoft requires `pg`.
- Repeated pages are not detected.
- Reaching `max_pages` exits without setting `truncated=True`.
- Broad exception handling also catches first-page failure, contrary to the intended invariant.
- Response validators are overwritten by later pages.
- Root metadata is mostly discarded except Tesla’s `lookup`.
- `str(None)` can become the external ID `"None"`.
- Invalid items can be silently skipped without making the run incomplete.
- Nested templates such as Apple’s `{locations[0].city}` and Uber’s `{location.city}` are incompatible with the current Python formatting approach.
- A partial `feed.config` replaces, rather than merges with, the built-in source configuration.

Until fixed, feeds using this adapter should not close postings by absence.

### 3. Lever is not necessarily a complete snapshot

The adapter notes that pagination exists but fetches only one response and never marks truncation. Either implement pagination or mark those feeds incapable of absence-based closure.

### 4. HTML and JSON-LD ingestion is not fully replayable

The generic JSON-LD adapter stores parsed objects rather than the original HTML/JSON-LD text. Jobvite similarly discards both board HTML and job-page HTML.

That is fragile because a parsing regression can produce zero jobs and look like a legitimate empty board. Store the HTML artifact and make parse failures affect coverage confidence.

JSON-LD’s last-resort external ID is a hash of the complete job object. Editing the description then changes identity, making the old posting “vanish” and a new one appear. Content hashes should version a posting, not normally identify it.

### 5. The fixture strategy needs lifecycle cases

The fixtures are useful real examples, but a reliable discovery system also needs scenarios for:

- two-page and final-short-page pagination;
- repeated pages;
- page-cap exhaustion;
- empty legitimate board versus challenge/error page;
- mid-list and mid-detail failure;
- duplicate IDs and duplicate location rows;
- missing/invalid IDs;
- the same job across ATS and aggregator;
- explicit closure, disappearance, and reopening;
- matched list/detail payloads for the same role;
- optional fields and schema changes.

The current Workday list and detail fixtures appear to represent different roles, making preservation and correlation tests weaker.

## Adapter consolidation recommendation

Consolidate transport mechanics, not all vendor semantics.

A maintainable split would be:

- JSON collection adapter: single-page or paginated list.
- List/detail adapter: list membership followed by cached detail hydration.
- Structured-document adapter: XML, JSON-LD, sitemap.
- Bespoke adapter: tokens, CSRF, GraphQL, unusual authentication or status rules.

Each vendor/company supplies a small specification plus narrowly scoped hooks for:

- request preparation;
- item path and stable ID;
- pagination advancement and termination;
- expected-count validation;
- list/detail correlation;
- explicit active/closed signals;
- canonical parsing.

Keep a bespoke adapter when the endpoint requires preflight tokens, rotating query IDs, cookies, unusual pagination, or schema-dependent logic. A universal configuration format should not try to represent behavior that actually requires executable hooks.

Most importantly, optimize list polling separately from detail fetching: reconcile disappearance from complete list snapshots, then fetch detail only for new, changed, or stale postings. That reduces network work while making closure detection more reliable.
