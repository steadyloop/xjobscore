# sources

Every source exposes two functions through the registry.

```python
async def harvest(feed: Feed, net: Net) -> Record
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

`harvest` imports `net` and `records` only. No database, no scheduling, no
scoring, no sibling source. Queueing, backoff, conditional GET, storage and
closure by absence already exist outside it.

`parse` is stricter: sync, pure, no `net`, no `net` even by omission -- it
never receives one. It is not called from `harvest` and not called during a
poll. `silver.py`'s replay pass calls it later, once per current
`raw_document` row, which is what makes bronze genuinely replayable: a parser
bug is fixed by running `silver.replay` again, never by re-fetching a posting
that may have closed since. Both functions may emit `Posting`; only `parse`
may also emit `Target`, `Label` or `Fact` -- `harvest` returns one `Record`
and nothing else.

## shared JSON API adapter

JSON API sources use the plain `CONFIGS` table in `json-api-configs.py`. The small
engine in `json-api.py` owns conditional list requests, pagination, optional detail
fan-out, payload demultiplexing and partial-result handling. Config values use
only paths, templates, fallbacks, joins, dates, lookups, lists and sections;
raw `Item` payloads retain vendor-specific data without parser hooks.

The registry binds each vendor name to `json-api.harvester(name)` and
`json-api.parser(name)`, so public source names and call signatures do not change.
## shared docxtract adapter

`docxtract.py` follows the same engine/config split for public job documents and feeds.
Its compact configurations support direct embedded JSON-LD, HTML link
discovery followed by JSON-LD detail pages, RSS/XML items, and static JSON
listing documents.  Jobvite, generic JSON-LD careers pages, Teamtailor, and
Simplify are profiles in `docxtract-configs.py`.

Jane Street (secondary-feed join) remains focused because it does not share
those document shapes.

Amazon, Apple, Tesla, and Uber are JSON API profiles. Company ownership does
not create a separate adapter type.

## harvest decides granularity

There is no second function *of `harvest`'s*, so nothing downstream of the
fetch sees the raw response shape -- `parse` sees only the demuxed `Item`
`harvest` already produced. Whatever "one item" means for your source is
settled inside `harvest`, once, and `parse` inherits that boundary.

| source | one item is |
|---|---|
| greenhouse, ashby, lever | one element of the JSON array |
| workday | one `jobPostings` element, across all pages |
| careers-site scrape | one JSON-LD `JobPosting` block on the page |
| hn_whoishiring | one top-level comment |
| simplify, cvrve, ouckah | one row of the listings table |
| github_oss | one contributor |
| yc, edgar_formd | one company record |
| gmail | one message |
| manual | *(none — the feed exists only to come due)* |

## Feed

What you are given. Read it; never write it.

| field | type | |
|---|---|---|
| `key` | `str` | locates the endpoint: a token, a URL, a Gmail query |
| `config` | `dict` | operator-owned: URLs, credentials, parameters |
| `state` | `dict` | engine-owned validators — pass to `net.get(state=...)`, nothing else |
| `id`, `source`, `enabled` | | identity and bookkeeping |
| `complete`, `closes`, `credentialed`, `cadence_s` | | operator flags; see below |

## Net

Retry, per-host rate limiting and backoff are already applied.

```python
await net.get(url, *, params=None, headers=None, state=None) -> Response
await net.json(url, **kw) -> Any      # get + .json(); raises on 304
await net.post(url, *, json=None, data=None, headers=None) -> Response

validators(response) -> dict          # module function, not a method on net
```

`Response`: `.status` `.headers` (lowercased) `.body` `.text` `.json()`
`.not_modified`. Raises `NetError` on anything that is not 2xx or 304.

Conditional GET is a few lines and the reason most polls cost nothing:

```python
from xjobscore.net import Net, validators

r = await net.get(url.format(key=feed.key), state=feed.state)
if r.not_modified:
    return Record(http_status=304, validators=validators(r))
...
return Record(items=items, http_status=r.status, validators=validators(r))
```

`http_status=304` is what tells `run.py` this poll matched — not an empty
`items[]`, which just means "the board is empty" and reports as `ok`.

`validators(r)` extracts `{etag, last_modified}` using the exact keys
`net.get(state=...)` reads back on the next poll. Never build that dict by
hand: the response header is `last-modified`, the state key is
`last_modified`, and getting it wrong fails silently — no error, conditional
GET simply never fires again and every poll refetches the whole board. It
omits keys the server did not send, so a 304 cannot erase a stored etag.
Return it on every path, 304 included; a `Record` without it drops the feed's
validators.

## Records

`harvest` peels the vendor-specific transport layer and demuxes the response
into single-listing raw units — it translates nothing. Field-by-field parsing
into the canonical vocabulary (postings, targets, labels, facts) happens
downstream of bronze, not inside a source.

One `harvest` call returns one `Record`. A `Record` bundles the demuxed
`items[]` — each one job or one detail fetch, verbatim — with `envelope`:
response-root facts no single item carries. Workable's company name lives
only at the response root, on no element, which is why `envelope` exists as
its own thing. It rides along on every `raw_document` row of the poll rather
than a row of its own — see Records below.

### Item

One demuxed unit, verbatim.

| field | type | default | |
|---|---|---|---|
| `external_id` | `str` | — | text always: int, uuid, slug and hash all appear |
| `payload` | `dict \| str` | — | exactly what the source received for this unit; no field dropped, no value converted |
| `kind` | `"json" \| "xml" \| "html" \| "mime"` | `"json"` | the vendor transport format `payload` arrived in, not this item's role — `json` → `payload_json`, everything else → `payload_text`. A source with a `str` payload must say which; router cannot tell xml from html from mime on its own. |

### Record

What one `harvest` call returns.

| field | type | default | |
|---|---|---|---|
| `items` | `list[Item]` | `[]` | one `Item` per listing |
| `envelope` | `dict` | `{}` | response-root facts no single item carries |
| `truncated` | `bool` | `False` | this fetch did not finish (a paginated board that died halfway); distinct from `feed.complete` |
| `http_status` | `int \| None` | `None` | |
| `validators` | `dict` | `{}` | etag / last_modified, written back to `feed.state` |

`payload` is exactly what the source received — nothing dropped, nothing
converted — which is what makes bronze genuinely replayable: a parsing bug
downstream is fixed by re-running over stored `Item`s, never by re-fetching a
posting that may have closed since.

## two rules that are easy to get wrong

You never close by absence. A source must never treat a paginated fetch that
died halfway as a complete listing — set `truncated=True` when it did.
Absence-driven closure is `reconcile`'s job alone, gated on `feed.complete`
and `feed.closes`, and it is not yours to trigger.

You catch `NetError` mid-pagination; you do not catch it on page one. `run.py`
catches every exception and turns it into outcome `error` — safe, but it
discards the `Record` entirely, so the pages you already fetched are thrown
away along with the validators. A multi-page source therefore wraps the paging
loop, and on failure returns what it has:

```python
try:
    while ...:
        page = await net.json(url, ...)
        items.extend(...)
except NetError:
    return Record(items=items, truncated=True, http_status=r.status)
```

That reports `partial`, which backs off exactly like `error` and is equally
barred from reconciling — the difference is that the items survive. Failing on
the first request has nothing to salvage; let it propagate.

## what belongs to the operator

`complete`, `closes`, `credentialed` and `cadence_s` are flags on the feed, not
facts your module asserts. URLs and credentials live in `feed.config`, which
you read and never write. A credentialed or incomplete
feed will not close anything, so authed and partial sources need no special
handling from you.

## parse

```python
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

Turns one demuxed `Item` (plus the poll's `envelope`) into zero or more of
the silver vocabulary — `Posting`, `Target`, `Label`, `Fact`, all defined in
`records.py`. One parser may emit both a posting and a discovery target from
the same item; there is no separate adapter/finder registry to split them
across.

- Takes `feed`, not just `item`, because the company name is not always on
  the payload: Ashby and Lever name it nowhere, so `parse` reads `feed.key`
  (the board slug) as `company_raw` instead. Symmetric with `harvest(feed,
  net)` for the same reason — the source knows facts the raw payload doesn't
  carry.
- Sets fields the `Posting` dataclass defines and nothing else.
  `title_normalized`, `content_hash`, `status`, `first_seen_at`, `group_id`
  are engine-owned and absent from `Posting` on purpose — a source reports
  what it saw, not what the database should conclude. See `silver.py`.
- A key that is missing or `None` on the payload becomes `None` on the
  `Posting`, never a raise. Real fixtures have optional keys — see
  `ashby-simple.json`'s second job, which lacks `department`, `team`,
  `isRemote`, `address`, `secondaryLocations` and `compensation` entirely.
- The long tail that has no `Posting` field goes in `source_extra`, verbatim.
  Multiple locations beyond the primary go in `locations_extra`. Sectioned
  descriptions (Lever's `lists[]`) go in `content_sections`.
- Secondhand sources (Simplify and kin) set `fidelity="secondhand"`,
  `origin_url` to the origin ATS URL, and `description_html=None` — a
  pointer row, not a copy of a body you never fetched. They emit no
  `Target`: deciding which source an arbitrary origin URL should be
  harvested as is unresolved (see ARCHITECTURE.md's cross-source identity
  open item), and guessing wrong here is worse than not guessing.
- Timestamp formats vary by vendor and are converted inline with the stdlib
  (`datetime.fromisoformat`, `datetime.fromtimestamp(ms / 1000, tz=UTC)`) —
  no shared date-parsing helper, which would be a sibling import `parse` is
  not allowed either.

## adding one

1. For a JSON API, add one compact entry to `json-api-configs.py`; for a compatible
   public job document or feed, add one to `docxtract-configs.py`. For another
   transport, write `<name>.py` exporting `harvest` and `parse` and register
   it explicitly.
2. Save one real response to `fixtures/<name>.json`; assert `harvest` on it
   the way the existing tests do (proves the demux found correct item
   boundaries and preserved each payload verbatim); assert `parse` against
   the same fixture's items as a pure function, no `Net` involved.

Removal is those lines in reverse. Nothing else in the tree names a source.

## invariants

- An enabled feed whose source is missing from `__init__.py` is an error, not a skip.
- Volatile keys — timestamps, nonces, view counts, signed URLs — are stripped at
  any depth before hashing, never from the payload, which stays verbatim. Miss one
  and every item looks changed every poll.
- A truncated listing must not be returned as if whole; a board you failed to page
  through is not evidence that the missing postings are gone. Report it: set
  `Record.truncated = True`.

## planned discovery sources

### Workable Jobs by Workable / SmartRecruiters job-seeker search

Cross-company public job marketplaces that can discover ATS company identifiers
from fresh role and location searches. A result URL carries the identifier:

- Workable: `apply.workable.com/<company-token>/job/<job-id>`
- SmartRecruiters: `jobs.smartrecruiters.com/<company-id>/<job-id>`

These are discovery sources, not replacements for the existing ATS adapters. The
identifier should be passed to the corresponding adapter for live validation.
Neither vendor currently documents an unauthenticated global-enumeration API;
automation would need an approved portal integration or permitted page/endpoint
collection. Employers also appear only when they publish to that marketplace.

### iCIMS hosted careers portal

The hosted portal, `https://{tenant}.icims.com/jobs/search`, is separate from
the implemented iCIMS vanity JSON board,
`https://careers.{company}.com/api/jobs`. A portal hostname must not be used to
derive that vanity hostname: for example, `campus-americas.icims.com` does not
imply that `careers.campus-americas.com` exists.

Revisit this when a live portal yields a stable, unauthenticated listing
endpoint or a compatible public document shape. Until then, treat portal hosts
as discovery targets, not fetchable iCIMS JSON feeds.
