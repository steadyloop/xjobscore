# Unified adapter engine

This package contains transport and mapping mechanics only. It deliberately
contains no source names, endpoint specifications, or registry side effects.
Construct an `AdapterSpec`, bind it with `bind()`, and expose the resulting
`harvest(feed, net)` and `parse(feed, item, envelope)` methods only when the
application's source registry is explicitly migrated.

## Pipeline

An adapter may use these stages:

1. An optional discovery request selects values for request context.
2. A list request runs once or follows an integer pagination contract.
3. Extraction filters and deduplicates rows while preserving each raw item.
4. An optional detail request runs for every listed row.
5. Pure output expressions map a replayed item into `Posting`, `Target`,
   `Label`, or `Fact` values.

Built-in response decoders cover JSON, HTML, schema.org JSON-LD, and XML.
XML extraction uses ElementTree paths, including expanded namespace names and
attribute paths such as `{namespace}link/@href`. HTML list pages use
`HtmlLinks`; JSON-LD detail pages use `ResponseSpec(format="jsonld",
schema_type="JobPosting")`.

Expressions provide references, fallbacks, joins, list mapping, conditions,
regular-expression predicates, XML selection, and named normalization. A
source-specific normalizer is appropriate for semantic interpretation such as
structuring a free-form community post. It must remain pure. Transport loops,
HTTP calls, pagination, and partial-result handling belong to the engine, not
to normalizers.

`DetailSpec(retain_list=True)` stores both received units under `list` and
`detail` in the raw item. Use it when canonical fields depend on list-summary
data that the detail response omits; this prevents hydration from silently
discarding source evidence.

## Completion and failures

The first list GET carries `feed.etag` for conditional requests. Later pages,
discovery, and detail requests do not. A first-page failure propagates; a
later-page or detail failure returns all salvaged items with
`Record.truncated=True`. Reaching `max_pages` while the response still reports
more data is also truncated. Therefore an incomplete fetch is never presented
as evidence that absent postings closed.

The executable teaching material is
`sources/tests/test_unified_v2_archetypes.py`. Its specs are synthetic and
cover single JSON collections, secondhand JSON, paginated list/detail APIs,
HTML-to-JSON-LD, RSS, Atom, bespoke XML, and discovery plus free-form parsing.
