# Job Resolution Strategy

This document defines how Xjobscore should preserve source data, detect a
posting that vanishes, and present one unified job board without destructive
cross-source merging.

## Core model

Keep source postings as immutable observations. Build the unified board as a
separate entity-resolution layer; do not overwrite or collapse source rows.

There are four distinct concepts:

1. **Feed**: one polling endpoint or board, with a stable identity independent
   of its adapter implementation.
2. **Posting observation**: one source's representation of a role. Its natural
   identity is `(source_instance, external_id)`.
3. **Job entity**: the logical role shown once on the unified board. Multiple
   observations may belong to it. Applications should attach to this stable
   entity, while recording the source posting or apply URL used.
4. **Posting evidence**: the reason two observations represent the same role,
   including method, confidence, evidence values, algorithm version, and review
   state.

`posting`, `posting_link`, and `group_id` are a good start. Add a real
`job_entity` table rather than treating a materialized group UUID as the only
representation of a unified job.

## Authoritative sources

An authoritative source directly controls a role's availability: normally the
employer's ATS or official careers site (for example Greenhouse, Lever,
Workday, Ashby, or the company's own careers API).

Aggregators, search engines, job-alert emails, and reposting sites are
non-authoritative. They can lag, omit, or misstate availability.

## Status resolution and disappearance

Resolve status first per source observation, then for the unified job entity.

| Evidence | Source observation | Unified role |
| --- | --- | --- |
| Missing from one valid full snapshot | `unlisted` | Usually remains open if another authoritative origin is open |
| Explicit `active=false`, `closed`, filled notice, or confirmed detail 404/410 | `closed` | May close the entity immediately if that source is authoritative |
| All authoritative origin observations are unlisted or closed | `closed` | `closed` |
| Authoritative sources conflict | Preserve both facts | `conflicting` / verify, or apply an explicit authority rule |

Do not equate `unlisted` with `closed`. Absence is evidence; an explicit source
closure is a direct assertion.

The precedence order is:

```text
explicit closure on canonical employer apply endpoint
> explicit closure on another employer-owned ATS endpoint
> valid absence from a complete authoritative board
> aggregator/source-copy closure
> aggregator/source-copy absence
```

Thus a canonical employer ATS can close its own observation and the unified
role even while an aggregator still displays a stale copy. If two independent
employer-owned sources disagree, retain the conflict; prefer the source that
owns the canonical application URL or requisition, but do not delete the other
observation.

### Snapshot membership

The unit for absence detection is a successful complete poll snapshot, not a
raw payload revision. Persist every observed external ID on every valid poll:

```text
poll_run_item(
    poll_run_id,
    feed_id,
    external_id,
    raw_document_id nullable,
    primary key (poll_run_id, external_id)
)
```

Reconciliation is then:

```text
previous successful complete snapshot IDs
− current successful complete snapshot IDs
= newly vanished IDs
```

Infer absence only when:

- `feed.complete = true` and `feed.closes = true`;
- the fetch, pagination, and all required detail fetches completed;
- the snapshot passed sanity checks; and
- the response was not a 304.

On the first valid absence, set the observation to `unlisted` and record
`absent_since`, `last_seen_at`, `last_seen_poll_run_id`, and
`absence_poll_run_id`. Close immediately only for highly reliable authoritative
feeds, or after a second complete poll/a grace interval for less reliable
feeds. If the same source ID returns, reopen the observation while retaining
its lifecycle history.

Quarantine suspicious snapshots before mass closure: for example, an apparent
drop from 2,000 roles to zero should require a repeat poll or manual approval.

### Current lifecycle gap

The present implementation has the correct capability concepts but does not
yet reconcile absence. `run.py` leaves reconciliation as a TODO. Also,
unchanged raw payloads do not create new raw revisions, so raw revision rows
cannot prove that a role was seen in a later poll. `silver.replay` therefore
cannot use a raw row's original poll ID as current-poll presence.

Explicit source closure is also parsed but not applied to stored lifecycle
status: `Posting.state`, `closed_evidence`, and `source_active` need a path
that updates the observation status and records the evidence.

## Cross-source entity resolution

Use a hierarchy of evidence, never one opaque fingerprint.

### Tier 1: deterministic links

Automatically link observations only with strong evidence:

- Same ATS provider, tenant/board, and ATS job ID extracted from URLs.
- Same canonical origin URL after safe normalization.
- Redirect convergence to the same final origin URL.
- Same resolved employer and employer requisition ID.
- An aggregator URL containing a known ATS tenant and external ID.

Store normalized identifiers explicitly:

```text
posting_identifier(
    posting_id,
    namespace,       -- greenhouse, lever, workday, employer_req
    scope,           -- tenant/board/company
    value,
    provenance
)
```

Never compare a bare external ID across feeds. `12345` has meaning only inside
the provider and board/tenant namespace that issued it.

### Tier 2: high-confidence composite links

Use these for candidate generation and calibrated automatic links only:

- Resolved employer identity.
- Strong normalized-title similarity.
- Compatible locations and workplace type.
- Compatible employment type.
- Publication dates in a reasonable window.
- Description similarity after boilerplate removal.
- Matching apply domain or careers path.

### Tier 3: review-required candidates

Company plus title is not enough: employers often have multiple distinct
"Software Engineer" openings with similar descriptions. Require review for
ambiguous matches.

Use negative evidence as well:

- Different requisition IDs for the same employer.
- Incompatible locations for location-specific roles.
- Different seniority or employment type.
- Widely separated publishing periods that indicate a repost.
- Evidence that one role replaced another.

The existing evidence-link design is valuable, but unrestricted connected
components let one poor fuzzy link merge whole clusters. Only deterministic or
very high-confidence links should participate in automatic transitive grouping.
Fuzzy links stay proposed until reviewed or constrained cluster checks pass.

Every link must retain the evidence details and `algorithm_version`, enabling
replay, auditing, calibrated thresholds, and safe reversal of bad merges.

## Adapter consolidation

Unify transport mechanics, not source semantics.

Shared components are appropriate for:

- Offset, page, and cursor pagination.
- Conditional requests.
- List-to-detail fan-out.
- JSON-path extraction.
- URL templates.
- Snapshot-completeness validation.
- Retry and rate-limit behavior.

Keep vendor-specific parsing, identifier extraction, volatile-field rules,
closure semantics, and source fidelity separate or declaratively configured.

Separate source identity from adapter implementation:

```text
provider/source identity: greenhouse, workday, amazon
adapter implementation: json_list_detail_v2, configurable_json_v1
```

An adapter refactor must not change a feed's identity, provenance, historical
external-ID namespace, or raw data interpretation. Persist an
`adapter`/`adapter_version` separately from provider and source-instance
identity.

### Configurable adapter hardening

Before expanding `inhouse`:

- Treat reaching `max_pages` before the reported total as `truncated`, never a
  successful complete snapshot.
- Keep first-page validators, rather than overwriting them on later pages.
- Catch expected network/transport errors, not broad `Exception`.
- Surface required-field parse failures rather than silently dropping items.
- Preserve an appropriate long-tail projection; its current `source_extra` is
  empty.

Track adapter health metrics:

- Invalid or missing IDs.
- Duplicate IDs in one snapshot.
- Parsed item count versus listed item count.
- Missing required canonical fields.
- Unexpected schema keys.
- Snapshot-count anomalies.
- Detail-fetch failures.

## Field retention

Preserve complete source observations in bronze ingestion, subject to security
and retention controls. Do not duplicate every field in silver/canonical
tables.

- **Bronze**: complete source observation, minimally redacted where required.
- **Silver**: commonly queried semantic fields.
- **`source_extra`**: selected long-tail fields useful for querying.
- **Unified entity**: presentation values selected by explicit
  provenance/quality rules, never by overwriting source observations.

For list/detail sources, preserve both responses namespaced rather than
flattening or discarding one:

```json
{
  "summary": { "...": "verbatim list item" },
  "detail": { "...": "verbatim detail response" }
}
```

Alternatively, persist separate raw documents linked to the same observation.
Namespacing prevents collision-based loss.

This applies to the current BambooHR, SmartRecruiters, Rippling, and Workday
adapters, which retain detail payloads but discard list summaries. Oracle
currently merges summary and detail dictionaries, which can overwrite same-name
fields.

"Preserve everything" does not mean retain unsafe data indefinitely. Redact,
encrypt, or exclude:

- Credentials and authorization material.
- Expiring signed URLs when they provide no audit value.
- Applicant data accidentally returned by an endpoint.
- Unnecessary personal email addresses.
- Tracking tokens.
- Data disallowed by source terms or retention policy.

Define retention, access control, and redaction policy before treating raw
storage as permanent.

## Fixture and test scenarios

Static fixtures are useful for parsing but cannot validate lifecycle or entity
resolution. Add temporal and cross-source scenarios:

- Poll 1, unchanged poll 2, missing role poll 3.
- Partial pagination.
- Empty successful board.
- 304 not modified.
- Detail 404/410.
- Reopened role.
- ATS migration with a new external ID.
- Aggregator plus ATS true duplicate.
- Same-company/same-title hard negative.
- Requisition-ID collision or reuse.
- Locale variants and multi-location duplicates.

These fixtures should drive lifecycle and deduplication regression tests, not
only field-mapping tests.
