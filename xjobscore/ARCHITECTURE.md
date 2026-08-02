# Architecture

Xjobscore sources from multiple feeds.
Each source is defined as having polling rate.

## Bronze
`harvest` output lands in `raw_document`, which `schema.sql` documents as
append-only and replayable (L1 INGEST). One row is stored per `Item`: payload
verbatim, hashed after volatile-key stripping so an unchanged poll inserts
nothing new. Because a source never drops a field or converts a value, a
parsing bug downstream is fixed by re-running over stored `raw_document` rows,
not by re-fetching a posting that may have closed since. That is what makes
"replayable" true rather than aspirational.

- `harvest` — retrieve and demux. Finds item boundaries, peels the transport
  envelope, translates nothing. Pure, standalone, complete alone. This is the
  single fetch.
- `reconcile` — diff a complete listing against last time, close what vanished.
  Opt-in per feed, never automatic.

### Sources

Each data source is limited to two functions. No DB access, schedule, policy decisions or knowledge of sibling sources.

It is designed generally to read anything — REST API, HTML, JSON-LD, email, RSS, PDF, LLM or human typing.

```python
async def harvest(feed: Feed, net: Net) -> Record
```
- Retrieve and demux. Finds item boundaries, peels the transport envelope. 

```python
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

### Records
| Field | Type | Meaning |
|---|---|---|
| `items` | list[Item] | One unit of job posting from the entire payload, verbatim for raw extraction. |
| `envelope` | dict | response-root facts no single item carries. |
| `truncated` | bool | Fetch failed (ex. a paginated board that died halfway); distinct from `feed.complete` |
| `http_status` | int | the HTTP status of the fetch |
| `validators` | dict | etag / last_modified, written back to `feed.state` |

### Item
| Field | Type | Meaning |
|---|---|---|
| `external_id` | str | raw_document.external_id |
| `payload` | dict \| str | payload_json / payload_text |
| `kind` | Literal["json", "xml", "html", "mime"] | raw_document.kind; vendor specific format |


`Posting`, `Target`, `Label` and `Fact` — the silver vocabulary — still exist.
They are what a raw `Item` becomes downstream of bronze, produced by a second,
per-source `parse` function that runs after `harvest`, not by `harvest`
itself. `harvest` never returns them; see Bronze, below, and Silver.

## Queue

A recurring feed and a one-off `Target` are the same row in the same table, differing
only by whether they repeat. Scheduling, backoff, and one-shot dispatch share one code
path and one policy function.

## Feed capabilities

A feed carries flags, not a source-type enum:

| Flag | Meaning |
|---|---|
| `complete` | The response is the full listing, so absence is meaningful |
| `closes` | Absence may close postings (requires `complete`) |
| `credentialed` | `config` holds credentials; failure is expected on expiry, best-effort |
| `cadence` | Base poll interval; backoff writes over it |

New source categories (pushed, authed, incomplete, manual) are flag combinations, never
new branches in the engine.

`feed.complete` and `Record.truncated` answer different questions and must not be
conflated. `feed.complete` is the operator's standing claim that this endpoint, when it
succeeds, returns the full listing. `Record.truncated` is `harvest`'s per-fetch report
that *this* attempt did not finish — a `complete` feed can still hand back a `truncated`
Record, and that must never be read as the complete listing it normally is.

## Router

Every source emits one `Record`, not four typed records, so a destination can no longer
be chosen by dispatching on record type. `router.route` does exactly one thing with it:
a `Record`'s `items` land in bronze (`raw_document`) as-is, hashed after volatile-key
stripping so an unchanged poll inserts nothing new. That is the whole module — one arrow,
`Record` → bronze, settled and covered by `tests/test_router.py`. `router.py` never
produces a `Posting`, never opens a database transaction beyond that insert, and is not
where turning a raw item into silver happens; see Silver, next.

## Silver

Where bronze becomes canonical. Each source that has one contributes a second function,
sitting beside `harvest` in the same `sources/<name>.py`:

```
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

Sync and pure — no `net`, no database connection, no clock. It reads one already-demuxed
`Item` (plus the poll's `envelope`) and returns zero or more `Posting` / `Target` /
`Label` / `Fact`. Same file as `harvest` because only the source knows its own payload
shape; a second, separate function because `harvest` and `parse` run on different
lifecycles — see the next paragraph. `sources/__init__.py` registers it in a second dict,
`PARSERS`, alongside `SOURCES`; see `sources/SOURCES.md`.

`parse` is never called from `harvest` and never runs inline with a poll. `silver.replay`
(`silver.py`) calls it, once per feed, over the *latest* revision of every `raw_document`
row that feed currently has — served by `store.latest_raw`, not a watermark or a cursor in
`feed.state`. That is the design decision this section used to leave open: a separate
replay pass, not parsing-inline-with-harvest, because a separate pass is what actually
makes bronze "replayable" (`schema.sql`'s own word) true. Parsing inline would not need
that property — a parser bug would still require re-fetching a posting that may have
closed since to fix. Re-running `silver.replay` is a no-op when nothing changed: it is
`store.upsert_posting`'s own `content_hash` comparison that makes replay idempotent, not
anything `silver.py` tracks itself.

Company resolution, `title_normalized` (`silver.normalize_title`, TR39-confusables-aware)
and the posting `content_hash` are computed in `silver.py`, not by any source's `parse` —
the same rule that keeps `title_normalized` off the `Posting` dataclass in the first
place. A `Posting` whose `company_raw` misses `company_alias` is parked in `company_new`
and not inserted; a `Target` reaches `store.upsert_feed`; `Label` and `Fact` are currently
dropped (`store.put_label`/`put_fact` still raise `NotImplementedError` — no destination
table yet).

`python -m xjobscore.run --parse` runs `silver.replay` over every feed once and exits.

## Modules

Ten files plus one folder.

| Module | Responsibility |
|---|---|
| `work.py` | What runs next and when. Queue and backoff in one pure function. |
| `records.py` | `Record`, `Item`, `Feed` — plus the silver vocabulary (`Posting` / `Target` / `Label` / `Fact`). No logic, no imports. |
| `net.py` | The only code touching the internet. Conditional GET, retry, rate limit. |
| `store.py` | The only code touching the database. Every SQL statement. |
| `sources/<name>.py` | One file per source implementing `harvest` and `parse`. `__init__.py` holds two name→function dicts, `SOURCES` and `PARSERS`. |
| `router.py` | `Record` → bronze (`raw_document`). One arrow, settled — see Router. |
| `silver.py` | Bronze → canonical: the replay pass that calls each source's `parse` and writes `posting` — see Silver. |
| `reconcile.py` | Closure by absence; honors foreign closure evidence. Not yet written — see Open items. |
| `score.py` | Pure: posting + labels + facts + profile → verdict and band. |
| `notify.py` | Delivery out, plus the daily heartbeat. |
| `run.py` | Config and the loop. The only module aware of all the others. |

Import direction is one-way: `records` ← everything; `sources/` → `net`, `records` only
(`parse` additionally sees only `records`, never `net`); `silver.py` → `sources`, `store`,
`records`; `run.py` → all.

## Flow

```
work.next() ──▶ harvest(feed, net) ──▶ Record ──▶ router.route ──▶ bronze (raw_document)

                                                                          ┊
                                                                          ┊  silver.replay(feed), separate schedule
                                                                          ▼
                                              raw Item ──▶ parse(feed, item, envelope)
                                                                          │
                                                     ┌────────────────────┼────────────────────┐
                                                     ▼                    ▼                     ▼
                                              Posting ──▶ store    Target ──▶ store       Label / Fact
                                              (posting)             (feed, one-off)        ──▶ store, dropped:
                                                  │                                        no table yet
                                                  ▼
                                            score ──▶ notify
```

`reconcile(feed)` runs after `harvest` only if `feed.complete` and `feed.closes` — but
`reconcile` closes canonical `Posting` rows, and `reconcile.py` itself is not written yet
(see Open items: it needs a "seen this run" signal that `silver.replay`'s
`last_seen_poll_run_id` stamping does not correctly provide). The line above states the
intended trigger, not a loop that runs end to end today. `score` and `notify` are likewise
unwired — `posting` rows now exist to feed them, but nothing calls either yet.

## Source mapping

Known sources against the contract. Every source emits the same thing — one `Record`
per `harvest` call — so "Emits" below describes what is inside that `Record` for each
category, not what silver type it produces. Only six of these (the direct-ATS row's
Greenhouse/Ashby/Lever, plus Workable/Breezy/Simplify) have a `parse` implemented today —
see Silver.

| Source | Emits | Flags |
|---|---|---|
| Greenhouse / Ashby / Lever / Workday / etc. | one `Record`; `items` = one job per array element (Workday: one `jobPostings` element, across all pages) | complete, closes |
| Careers-site JSON-LD scrape | one `Record`; `items` = one `JobPosting` block in the page's JSON-LD | complete, closes |
| Bespoke quant portals (Jane Street) | one `Record`; `items` = one element of the board's own JSON | complete, closes |
| GitHub internship lists (Simplify, cvrve, vanshb03) | one `Record`; `items` = one row of the listings table | complete, not closes |
| Gmail job alerts | one `Record`; `items` = one message | credentialed, not complete |
| YC Work at a Startup | one `Record`; `items` = one company record | credentialed |
| UCSD portal / Handshake | one `Record`; `items` = one job per array element | credentialed |
| HN "Who is hiring" (monthly) | one `Record`; `items` = one top-level comment | not complete, not closes |
| OSS-org → employer mapping | one `Record`; `items` = one contributor | one-off |
| Funding-round feeds | one `Record`; `items` = one company record | recurring |
| YC directory | one `Record`; `items` = one company record | recurring |
| Conference sponsor lists | one `Record`; `items` = one sponsor listing | one-off |
| Crowd-tracker company diff | one `Record`; `items` = one row of the tracked list | recurring |
| Segment hand-lists | one `Record`; `items` = none — the feed exists only to come due | manual, config file |
| Referrals, cold email, alumni contact | not a `harvest` source — a hand-written `Fact`, entered through whatever the (unresolved) routing path turns out to be | manual, via router |

Blocklisted, never implemented: LinkedIn, Indeed, Glassdoor, Google Jobs.

## Constraints on harvest

- Single fetch and recurring poll share one code path (`harvest`; `reconcile` optional).
- Credentialed and incomplete feeds never trigger closure.
- A truncated listing must be reported via `Record.truncated`, never silently treated
  as whole.

## Constraints on the parsing layer

These bind every source's `parse` and `silver.replay`, which together turn raw `Item`s
into the silver vocabulary. None of them are enforced by `harvest`, which never sees a
`Posting`, `Target`, `Label`, or `Fact`.

- One parser may emit both postings and discovery targets from the same source — no
  separate adapter/finder registries.
- Secondhand data is promoted by re-fetch at origin, never trusted as canonical.
- Closure evidence can arrive from a source that does not own the posting.
- Labels carry provenance, so source-supplied facts (`terms`, `sponsorship`) outrank
  model-inferred ones.
- Company-level facts reach the scorer, so "referral in hand" and cooldown state are
  scoreable.

## Open items

- Title normalization: `silver.normalize_title` folds a small, hand-seeded table of Lisu
  look-alikes (U+A4DF/A4E1/A4E3 → M/L/R) observed in Jane Street titles, not the full
  Unicode TR39 confusables table. Extend it only against an observed title, same as
  `router.py`'s `VOLATILE` table.
- Closure by absence cannot use `posting.last_seen_poll_run_id` as `silver.replay` writes
  it. `replay` stamps it from the bronze row's *own* `poll_run_id` — the run that last
  changed that row's hash — not from a live run `replay` is part of, because `replay` has
  no poll_run of its own. An unchanged item inserts no new bronze row (the point of
  `raw_document`'s UNIQUE constraint), so that stamped run id goes stale the moment a
  posting stops changing but keeps being seen. `reconcile.py` must get "seen this run"
  from somewhere else — most likely `router.route` returning the external_ids it saw, for
  `run.poll` to write onto `posting` directly. Nothing breaks today because `reconcile.py`
  does not exist; this must be solved before it is written.
- Cross-source posting identity: one requisition seen via ATS, aggregator, and email
  must collapse to one row. Candidate keys: embedded ATS id in URL, redirect
  convergence, content hash. This is also why secondhand sources (Simplify) emit only a
  `Posting` with `origin_url` set and no `Target` yet — guessing which source an origin
  URL should be harvested as is exactly this open item.
- Application tracker: submissions, OA/loop capacity ledger, band rationing. Consumes
  `score.py` output; not yet designed.
- Whether `--parse` should run inline at the end of each poll cycle, or stay the fully
  separate invocation it is today. Starts separate; nothing currently forces a merge.
