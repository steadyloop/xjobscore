# sources

Each datasource gets a module with two functions.

    fetch(feed, http)             -> Fetched                  produce the items
    parse(item, feed, envelope)   -> Posting | Lead | None     read one item

Hashing, bronze, company resolution, upsert, closure, scheduling and backoff are
generic and live in `xjobscore/ingest/`. A source may not touch the database,
import a sibling, or score anything.

## fetch decides what an item is

`parse` never sees the response. This is what makes non-ATS sources fit.

| source | one item is |
|---|---|
| `greenhouse` `ashby` `lever` | one element of the JSON array |
| `workday` | one `jobPostings` element, across all pages |
| `inhouse` | one node matched by the selector in `feed.config` |
| `hn_whoishiring` | one top-level comment |
| `simplify` `cvrve` `ouckah` | one row of the listings table |
| `github_oss` | one contributor |
| `yc` `edgar_formd` | one company record |
| `gmail` | one message |
| `manual` | *(none — the feed exists only to come due)* |

## parse returns one of three things

The engine routes on type. A source never declares a class and never writes a row.

| return | lands in |
|---|---|
| `Posting` | `posting`, via bronze → resolve → silver → closure |
| `Lead` | `company_new`, and `feed_ats('planned')` when it carries `ats` + `token` |
| `None` | nothing; item skipped |

A source may return both types from different items. That is how a tracker gains
posting output later without changing its registration.

## shapes

    Source                          the record a module exports as SOURCE
      name             str          == feed.source == feed_ats.ats
      fetch            Fetch
      parse            Parse
      poll_interval_s  int   = 900  default cadence for feeds of this source
      kind             str   = json 'json' | 'text' — which bronze column
      volatile         tuple = ()   keys stripped before hashing, any depth
      due              fn?   = None calendar rule; overrides poll_interval_s

    Feed                            in — one row of the feed table
      id, source, key
      config           dict         operator-owned: URLs, selectors, credentials
      state            dict         pipeline-owned: etag, cursor, failures
      parts()                       {key}, {t0}, {t1}, {t2} for URL interpolation

    Fetched                         out of fetch
      items            list         verbatim; whatever parse will be handed
      envelope         dict?        response-level facts parse needs
      http_status      int
      complete         bool  = True False ⇒ run is 'partial' ⇒ closure skipped
      not_modified     bool  = False 304; healthy poll, no work
      etag, last_modified

    Posting                         out of parse — what the source *stated*
      external_id, title, url_posting          required
      …                                        all else sparse → models.py

    Lead                            out of parse — a company worth tracking
      company_raw      str          required; the string resolution runs on
      domain, ats, token, url, extra           all optional

## adding a source

1. write `<name>.py`, ending in `SOURCE = Source(...)`
2. import it in `__init__.py`, add it to `SOURCES`
3. save one real response to `fixtures/<name>.json`, assert `parse` on it

Removing a source is the same two lines in reverse. Nothing else in the tree
mentions a source by name.

## invariants

- An enabled feed whose source has no entry in `SOURCES` is an error, not a skip.
- Credentials and per-feed knobs go in `feed.config`; the pipeline never writes it.
- A missed `volatile` key makes every item look changed on every poll — watch
  `items_changed / items_listed`.
- `complete=False` is honest and load-bearing: a board not fully listed is not
  evidence that the missing postings are gone.