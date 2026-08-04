# Source feed catalogs

One TOML file per adapter lists its known endpoint instances. The filename and
top-level `source` must match the public source name.

```toml
schema_version = 1
source = "greenhouse"

[[feed]]
key = "company-board-token"
company_id = "company"
company = "Company Name"
domain = "company.example"
status = "live"
enabled = true
validated_at = "2026-08-03T12:00:00-0700"

# Only when a source requires public per-board settings beyond its key.
[feed.config]
token = "public-board-token"
```

`key` is source-local: the same company can have a different key in every
catalog. `enabled` controls whether an entry is eligible for fetching after
validation. `live` means the source's public endpoint was validated and is the
only status the standalone inspector materializes as a transient feed.
Keep `planned`, `absent`, and `dead` entries when discovery needs to retain
their history; they are not fetched by the inspector.
