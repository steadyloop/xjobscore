# Source registry

`xjobscore/registry/` holds declarative data only -- the TOML documented here plus
the job-posting YAML read by `degrees.py`, `skills.py`, and `opportunity.py`. Every
path below is relative to that directory.

`companies.toml` is a snapshot of the canonical company and alias inventory.
Company IDs are the positive integer primary keys from PostgreSQL's `company`
table. `aliases` preserves the normalized spellings used to match discovered
companies as a simple string list seeded
with the company's canonical slug. Every company explicitly records a domain
(`unknown` when absent), an industry (`unknown` when unclassified), a `desc`
string, and a boolean `verified` state. An optional `stage` field
(`startup`, `growth`, `public`, `private`, `nonprofit`, or `unknown`) records
company maturity; omitting it defaults to `unknown` so an unclassified company
never asserts that it is not a startup.

An optional `excluded = true` retires a company from the active registry --
typically because a CS/math or biotech graduate has no plausible role there:
no software product, no engineering or R&D function, no lab or scientific
work. Common clusters: recruiting/HR agencies (place candidates, staff is
recruiters only), marketing/creative agencies (paid media, SEO, content, ad
creative production), media/content production (film, TV, comedy, branded
content, newsletters), care & education delivery (ABA/therapy providers,
schools, clinician staffing), physical trades & service (installation,
remodeling, summer camps), and advisory & membership (consulting, public
affairs, executive networks). An excluded row is validated exactly like an
active one -- same required fields, same alias and domain uniqueness checks
against the whole file -- it is simply omitted from `companies()`, so any
feed still pointing at it is skipped by `entries()` rather than seeded.
Retiring a company is a one-line edit, not a move to a second document, and
reversing the decision is deleting that line: the row keeps its original ID
and full data throughout.

`feeds.toml` is the complete human-readable inventory of feed source
implementations. The source type is encoded by its array name:

- `[[ats]]`: a multi-tenant recruiting-system vendor.
- `[[direct]]`: a first-party employer careers source.
- `[[board]]`: an independent job, program, or community board.

Each ATS source points to one tenant-feed catalog under `ats-catalog/`. The
catalog filename matches the source name; `ashby.toml` intentionally remains one
file. A vendor may register more than one source when the same endpoint is
harvested at a different fidelity -- one source fetching a detail page per
posting while another reads only the listing -- and each is a full source with
its own name, description, and catalog. They are never two views of one catalog:
a catalog declares exactly one `source`, and the loader rejects a second source
pointing at it.

Workday was that arrangement until the detail leg was retired: it cost ~91% of
every run's requests and derived neither `job_type` nor `remote`, so its tenants
moved into `workday-list.toml` and the `workday` source was removed.

A Direct source is exactly one feed, so its feed fields are flattened into the
`[[direct]]` declaration. A Board source is also exactly one feed and uses
the same flattened layout in its `[[board]]` declaration.

Every source declaration carries a `description` saying what the source is,
and an optional `implemented` boolean recording whether a working adapter for
it exists yet. Both are declared once, on the source, and never repeated on a
feed: an ATS description is written beside its `catalog` pointer in
`feeds.toml`, not on each of that vendor's tenant rows, and every feed the
source produces inherits it. An ATS declaration is otherwise just `name` and
`catalog`; Direct and Board declarations are flattened inline and add only
what runtime reads -- `verified`, and for a Direct `company_id`. The registry carries
no `enabled` flag: verified entries are seeded as feeds, while whether a seeded
feed is polled is operator state in PostgreSQL's `feed.enabled`, owned by the inspector.
A registry seed sets that column on first insert and never overwrites it after.
Adapter selection and source-spec registration are owned by runtime code, so
`feeds.toml` does not duplicate adapter names or config-file pointers. Optional
per-feed configuration remains operational: use
`[direct.config]` or `[board.config]` for flattened feeds and `[feed.config]`
inside an ATS catalog.

`xjobscore/registry_loader.py` is the shared validated interface. Use `companies()` for canonical
company identity, `source_types()` for taxonomy, `source_descriptions()` for
each source's description (including sources with no live feed), `entries()`
for all feed metadata, or `feeds()` for verified feeds. Every feed must reference a
known company ID; consumers should not parse the TOML directly.

Company identity is never declared on a feed. The loader resolves
`company_name` from `companies.toml` through `company_id`, so the two copies
cannot disagree -- a feed row asserting its own company name is exactly how
the HN feed came to be labelled "Hacker News" while its ID pointed at Y
Combinator. `company_name` and `domain` in ATS catalogs are still parsed for
now, but they are ignored and are due to be deleted.

`key` identifies the endpoint and forms the feed's primary key with `source`.
For an inline Direct or Board it defaults to `name`, so it is written out only
when the adapter needs a different value -- `simplify` declares
`key = "SimplifyJobs/Summer2027-Internships"` because its docxtract spec
interpolates that owner/repo path into a raw.githubusercontent URL.

A Board declares no `company_id` at all. It resolves an employer from each
posting rather than from the feed (`silver._company_id` returns the feed's
company only for ATS and Direct), so a feed-level company would mislabel every
posting it carried. `description` is what names a Board instead; it is required
there, optional on a Direct, and reaches `Feed.description` through the registry
the same way `config` and `source_type` do, without a database column.

`implemented` is documentation, not a gate: whether a feed is polled still
follows from `verified`. Note that the inspector separately derives
`source_registered` and `parser_registered` from `sources.SOURCES` and
`sources.PARSERS`, so those two answer the same question from the code itself.

Inline Direct and Board fields use this canonical order:

```toml
[[board]]
name = "example"           # resolves to a harvest function in sources.SOURCES
key = "owner/repo"         # optional; defaults to `name`
description = "What this board is, and any operator caveat."
verified = true            # endpoint verification state
implemented = false        # optional; omit when an adapter works

[board.config]             # optional adapter-specific settings come last
```

ATS catalog feeds keep the longer legacy sequence -- `key`, `verified`,
`verified_at`, `company_id`, `company_name`, `domain`, then `config`. A catalog
row with `company_id = 0` is an unresolved discovery candidate: a tenant token
observed in the wild but not yet tied to a canonical company. The loader
ignores those rows until someone resolves them.

`xjobscore/feader.py` consumes those validated objects for PostgreSQL. It
upserts companies with their exact registry IDs and aliases, then seeds only
verified feed entries as ATS tokens and feeds. It advances the company identity
sequence beyond explicit registry IDs. Run it through
`python -m xjobscore.run --seed`; add `--dry-run` to validate and print counts
without writing.
