# Source registry

`companies.toml` is a snapshot of the canonical company and alias inventory.
Company IDs are the positive integer primary keys from PostgreSQL's `company`
table. `aliases` preserves the normalized spellings used to match discovered
companies as a simple string list seeded
with the company's canonical slug. Every company explicitly records a domain
(`unknown` when absent), an industry (`unknown` when unclassified), a `desc`
string, and a boolean `verified` state.

`feeds.toml` is the complete human-readable inventory of feed source
implementations. The source type is encoded by its array name:

- `[[ats]]`: a multi-tenant recruiting-system vendor.
- `[[direct]]`: a first-party employer careers source.
- `[[board]]`: an independent job, program, or community board.

Each ATS source points to one tenant-feed catalog under `ats-catalog/`. The
catalog filename matches the source name; `ashby.toml` intentionally remains one
file. A Direct source is exactly one feed, so its feed fields are flattened into
the `[[direct]]` declaration. A Board source is also exactly one feed and uses
the same flattened layout in its `[[board]]` declaration.

An ATS declaration contains only `name` and `catalog`. Direct and Board
declarations contain `name` plus the standard feed fields. The registry carries
no `enabled` flag: verified entries are seeded as feeds, while whether a seeded
feed is polled is operator state in PostgreSQL's `feed.enabled`, owned by the inspector.
A registry seed sets that column on first insert and never overwrites it after.
Adapter selection and source-spec registration are owned by runtime code, so
`feeds.toml` does not duplicate adapter names or config-file pointers. Optional
per-feed configuration remains operational: use
`[direct.config]` or `[board.config]` for flattened feeds and `[feed.config]`
inside an ATS catalog.

`loader.py` is the shared validated interface. Use `companies()` for canonical
company identity, `source_types()` for taxonomy, `entries()` for all feed
metadata, or `feeds()` for verified feeds. Every feed must reference a
known company ID; consumers should not parse the TOML directly.

Every feed lists its endpoint identity first (`key`),
then verification metadata (`verified`, `verified_at`), then resolved company
metadata (`company_id`, `company_name`, `domain`). `verified_at` is an empty
string when no historical verification timestamp was recorded. Adapter-only
parameters remain last in `config` or the optional `feed.config` table.

Feed fields use this canonical order:

```toml
[[feed]]
key = "tenant-token"       # ATS tenant/board token
company_id = 42            # canonical company relationship
company_name = "Example"  # human-readable company identity
domain = "example.com"
verified = true            # endpoint verification state
verified_at = "2026-08-10"

[feed.config]              # optional adapter-specific settings come last
```

Keyscan may also append unresolved discovery candidates to ATS catalogs. They
use the complete canonical field sequence with `verified =
false`, `verified_at = ""`, `company_id = 0`, `company_name = ""`, and
`domain = ""`. The loader intentionally ignores these zero-valued candidate rows until
they have been resolved to a canonical company.

`xjobscore/feader.py` consumes those validated objects for PostgreSQL. It
upserts companies with their exact registry IDs and aliases, then seeds only
verified feed entries as ATS tokens and feeds. It advances the company identity
sequence beyond explicit registry IDs. Run it through
`python -m xjobscore.run --seed`; add `--dry-run` to validate and print counts
without writing.
