# Corpus-grounded filter resolution

## Purpose

This project resolves messy filter values with a precision-first, corpus-grounded process. It is intended for debugging and early use against a known snapshot of the current Silver table, not as a universal geocoder or an assertion that every possible value has been understood.

The primary safety requirement is asymmetric: leaving a value unresolved is acceptable, but incorrectly filtering out an in-scope posting is not. For the location filter, that means protecting US opportunities from false non-US classification.

## Terms

- **Raw value:** An exact value observed in the current Silver field, such as `location_text`.
- **Canonical value:** The normalized value under which one or more raw values are counted.
- **Anchor:** A raw value that provides direct evidence for a canonical value, such as `Athens, Greece`.
- **Collision:** A name that can refer to more than one canonical value, such as Athens in Greece, Georgia, or Texas.
- **Residual:** The raw values that remain after the accepted rules in a layer have been applied.
- **Abstention:** Deliberately leaving a value unresolved because the available evidence does not support a safe classification.

## Governing principles

1. Work from the exact current corpus placed in scope.
2. Inventory exact raw values and posting counts before creating rules.
3. Derive canonical anchors from evidence present in the corpus rather than silently importing geographic knowledge.
4. Apply the strongest, least ambiguous evidence first.
5. Keep every rule explainable through the raw values that support it.
6. Treat collisions as a separate problem; never let a city-name match decide a country by itself when a known namesake exists.
7. Prefer false negatives to unsafe false positives.
8. Remove resolved values from the residual set and rerun subsequent layers only against what remains.
9. Revisit earlier assignments when a later layer discovers a new collision.
10. Preserve an explicit unresolved remainder. Unresolved is a valid result, not a processing failure.
11. Reconcile counts after every layer so no posting silently disappears or is counted incorrectly.

## Resolution workflow

### 1. Freeze the corpus

Define the precise source table, row status, opportunity types, and snapshot being resolved. The resulting corpus count is the accounting boundary for every later step.

For location resolution, the input is the exact `location_text` stored on current `job_posting` rows. Bronze payloads, parser replays, external gazetteers, or hidden enrichment fields are not substituted for that value unless a later rule explicitly authorizes them.

### 2. Build an exact-value inventory

Group by the raw field without normalization and retain both the exact spelling and posting count. Whitespace, punctuation, ordering, and repeated components remain visible at this stage because they may require different rules.

### 3. Establish high-confidence anchors

Start with values that contain direct, internally consistent evidence. For example, a country-last value such as `Athens, Greece` establishes an Athens/Greece anchor.

Country-leading, facility-like, regional, numeric, generic, and multi-place values are not accepted merely because they resemble a known pattern. They require independent support or remain unresolved.

### 4. Expand from established anchors

Search the residual values for literal, case-insensitive occurrences of established canonical cities. A raw value is folded into a canonical entry only when the match is safe under the current collision rules.

Each canonical entry retains its supporting raw values and counts. A posting contributes at most once to a given canonical city even if the city name repeats in its raw value.

### 5. Audit collisions

Before using a city as a filter value, search for evidence that the same name occurs in another country or jurisdiction. Collisions are held out of the ordinary city-expansion layer.

Examples include:

- Athens: Greece, Georgia, and Texas
- Vancouver: British Columbia and Washington
- Cleveland: Queensland, Mississippi, and Ohio
- Paris: France, Illinois, and Texas
- San Jose: Costa Rica and California

A mixed-location posting that contains both US and non-US cities must not be classified as safely non-US.

### 6. Apply stronger collision discriminators

Use a later, explicit layer to resolve values that carry stronger evidence. For US collisions, an explicit state is sufficient to create a state-specific canonical entry:

- `Athens, TX` becomes `Athens, Texas, United States`.
- `USA, GA, Athens` becomes `Athens, Georgia, United States`.
- `Vancouver, WA` becomes `Vancouver, Washington, United States`.
- `Cleveland, MS` and `Cleveland, OH` remain separate state-specific entries.

State abbreviations are expanded in the canonical label so the result records the evidence used to disambiguate the city. A bare colliding name is not assigned by this rule.

### 7. Rerun against the residual

After confirmed US values are removed, rerun the non-US city matcher against the new residual set. Collapse a remaining value only if the US collision has been eliminated and the value is demonstrably associated with the non-US canonical entry.

Values such as bare city names, province-abbreviation-only forms, or mixed US/non-US location lists remain unresolved unless a separately approved evidence rule validates them.

### 8. Add narrow domain rules explicitly

Some facts justify a small, named exception rather than a general heuristic. Singapore is both a country and a city, so Singapore-only values can be canonicalized as `Singapore, Singapore`. Values that also name another city remain unresolved as multi-place values.

Exceptions must state their scope and must not silently broaden the evidence accepted by other rules.

### 9. Abstain and report

The final output includes:

- canonical values grouped by country or other filter dimension;
- every exact raw value used as evidence and its count;
- unresolved values partitioned by reason;
- collision values that still require manual review; and
- summary counts that reconcile to the original corpus.

## Accounting invariants

- `resolved postings + unresolved postings = corpus postings` when those groups are defined as mutually exclusive posting-level partitions.
- Canonical city counts may not be summed as a corpus total when a multi-city posting legitimately contributes to more than one city.
- Removing a raw value from an unresolved section requires adding the same posting count to a documented resolved destination.
- One raw value may not be assigned to conflicting countries without being marked as multi-place or conflicting.
- A newly discovered collision triggers revalidation of earlier bare-name assignments.
- Every rule records its layer and evidence so the result can be reproduced and audited.

## What kind of method this is

The individual techniques are established:

- deterministic entity resolution;
- seed-and-expand or bootstrapped canonicalization;
- sieve- or cascade-based classification;
- high-precision weak supervision;
- human-in-the-loop residual analysis; and
- selective classification with abstention.

The project-specific method is the combination and ordering of those techniques: learn anchors from the current corpus, apply one evidence layer at a time, protect the US class through explicit collision audits, retain exact evidence, and preserve unresolved values rather than forcing coverage.

A concise name for the methodology is **corpus-grounded, precision-first resolution with iterative collision sieves and abstention**.

## Scope and maintenance

These mappings are spot filters for the corpus on which they were validated. They are not permanent geographic truth. New postings can introduce new spellings, cities, collisions, or multi-location forms, so each refreshed corpus must be inventoried and passed through the same layers again.

Increasing coverage requires a new explicit evidence rule and a fresh false-positive audit. It must not be achieved by quietly weakening an existing rule.
