# Sources

Every registered source exposes two functions:

```python
async def harvest(feed: Feed, net: Net) -> Record
def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]
```

`harvest` retrieves and demultiplexes vendor data. It has no database or
scheduler access and must not translate canonical fields. `parse` is sync and
pure: it maps one replayed raw item into parser vocabulary without network,
database, or clock access.

## Feed and network contract

A source may read:

| Field | Meaning |
|---|---|
| `source`, `key` | Adapter identity and endpoint/tenant key. |
| `config` | Validated registry-owned URLs, headers, credentials, and parameters. |
| `company_id`, `company_name`, `source_type` | Registry company metadata used downstream. |
| `etag` | The only persisted conditional-request validator. |

Interval, strikes, due time, and lease are scheduler fields and should be
ignored by adapters.

```python
r = await net.get(url, params=params, headers=headers, etag=feed.etag)
if r.not_modified:
    return Record(http_status=304, etag=etag(r) or feed.etag)
return Record(items=items, envelope=envelope, http_status=r.status, etag=etag(r))
```

`Net` applies per-host throttling and retries transport errors, 429s, and 5xx
responses. A 304 is successful. Other final 4xx responses raise `NetError`.
Later pagination/detail failures should return salvaged items with
`truncated=True`; first-request failures should propagate.

## Raw contract

`Record.items` contains one verbatim unit per listing. `Record.envelope`
contains response-root facts no individual item carries.

| Item field | Meaning |
|---|---|
| `external_id` | Vendor-local ID, converted to text but otherwise unchanged. |
| `payload` | Verbatim JSON object or XML/HTML/MIME string. |
| `kind` | `json`, `xml`, `html`, or `mime`. |

The router stores each item in `raw_posting.data` with the versioned wrapper
`{version, kind, payload, envelope}`. Parsers therefore receive the same
payload and envelope during normal polling and `--parse` replay.

## Parse contract

Parsers return zero or more `Posting`, `Target`, `Label`, or `Fact` values.
Only origin `Posting` values currently reach `job_posting`:

- Keep the adapter's external ID in `Posting.external_id`.
- Put HTML in `description_html`; silver owns plain-text conversion.
- Keep raw provider values in `company_raw`, `location_raw`,
  `workplace_type_raw`, and `source_extra`.
- Set `state="closed"` or `source_active=False` only from explicit source
  evidence. Absence from a listing is not closure.
- Mark copied/aggregated jobs `fidelity="secondhand"`; they remain bronze-only.

ATS/direct company fallback and board alias resolution belong to silver, not
the parser. `Target`, `Label`, and `Fact` are retained for compatibility but
are counted as unsupported because `schema.sql` has no destination tables.
Targets never create feeds; the registry owns inventory.

## Shared adapters

JSON APIs normally add a profile to `json-api-configs.py`. Public JSON-LD,
HTML, RSS, Atom, XML, and static documents normally add a profile to
`docxtract-configs.py`. Use a focused module only for a genuinely novel
transport or semantic parser. The unified v2 engine is available for more
structured transport/mapping specifications.

## Adding a source

1. **Decide to implement.** Add and validate the company/feed entry under
   `sources/registry/`.
2. **Prefer the shared adapters.** Add a profile to `json-api-configs.py` or
   `docxtract-configs.py` (see "Shared adapters" above). Write a focused
   `harvest`/`parse` module only when the transport is genuinely novel, and
   register it explicitly in `sources/__init__.py`. Save representative
   fixtures and test request construction, demultiplexing, partial behavior,
   raw fidelity, and canonical parser output. Confirm conditional requests
   pass `feed.etag` only and that every returned item has a stable external
   ID.
3. **Document quirks and envelope meaning — only if they exist.** Most
   sources need zero prose here: a comment is a signal that something is
   non-obvious, not a checklist item. `ashby`, `greenhouse`, `lever`, and
   `workable` carry no comments at all — their `"envelope": {...}` line
   speaks for itself. Compare `successfactors`, whose config entry explains
   *why* two RSS fields are dropped and that `apply_url` isn't derivable, and
   whose `ats-catalog/successfactors.toml` explains that `key` is a hostname,
   not a board slug. When a source has something a future reader genuinely
   needs to know, write one short WHY comment colocated at the entry:
   - Config-driven source → a `#` comment above its entry in
     `json-api-configs.py` / `docxtract-configs.py`.
   - Registry/key-level quirk → a `#` comment above `[[feed]]` in its
     `ats-catalog/{source}.toml`.
   - Bespoke module → the module docstring, reinforced with an inline
     comment at the exact line handling the quirk if that helps (see
     `planned/janestreet.py`).

LinkedIn, Indeed, Glassdoor, and Google Jobs remain blocklisted by design.
