from __future__ import annotations

import tomllib

import pytest

from xjobscore.registry_loader import (
    COMPANY_STAGES,
    REGISTRY_DIR,
    RegistryError,
    companies,
    entries,
    feeds,
    source_descriptions,
    source_types,
)


_INLINE_FIELD_ORDER = (
    "key", "description", "company_id", "verified", "implemented", "config",
)
_CATALOG_FIELD_ORDER = (
    "key",
    "verified",
    "verified_at",
    "company_id",
    "company_name",
    "domain",
)


def _inline_row(table: str, **overrides) -> str:
    """Return a minimal valid inline source row for ``table``."""
    fields = {"key": '"demo-board"', "description": '"Demo source."', "verified": "true"}
    if table == "direct":
        fields["company_id"] = "1"
    fields.update(overrides)
    body = "\n".join(f"{key} = {value}" for key, value in fields.items())
    return f'schema_version = 2\n[[{table}]]\nname = "demo"\n{body}\n'


def _write_companies(path):
    (path / "companies.toml").write_text(
        '''
schema_version = 2
[[company]]
id = 1
name = "Demo"
aliases = ["demo"]
domain = "unknown"
industry = "unknown"
desc = ""
verified = false
'''
    )


def test_registry_loads_all_declared_feeds():
    loaded = entries()
    live = feeds()

    assert loaded
    assert len(live) == sum(entry.verified for entry in loaded)
    assert loaded == tuple(sorted(loaded, key=lambda entry: (entry.source, entry.key)))


def test_every_feed_resolves_to_a_canonical_company():
    """Boards resolve their employer per posting, so they declare no company."""
    company_ids = {company.id for company in companies()}
    declared = {
        entry.company_id for entry in entries() if entry.company_id is not None
    }

    assert company_ids
    assert declared <= company_ids
    assert all(
        entry.company_id is None
        for entry in entries()
        if source_types()[entry.source] == "Board"
    )


def test_company_aliases_are_flat_norm_lists_seeded_with_slug():
    ambient = next(company for company in companies() if company.id == 97)

    assert ambient.aliases == ("ambient-ai", "ambient.ai")
    assert all(isinstance(alias_norm, str) for company in companies() for alias_norm in company.aliases)


def test_company_industries_are_explicit():
    assert all(company.industry for company in companies())


def test_company_stages_are_within_the_vocabulary():
    assert all(company.stage in COMPANY_STAGES for company in companies())


def test_company_stage_defaults_to_unknown_when_absent(tmp_path):
    _write_companies(tmp_path)

    assert companies(tmp_path)[0].stage == "unknown"


def test_registry_rejects_an_unrecognized_stage(tmp_path):
    (tmp_path / "companies.toml").write_text(
        '''
schema_version = 2
[[company]]
id = 1
name = "Demo"
aliases = ["demo"]
domain = "unknown"
industry = "unknown"
stage = "seed"
desc = ""
verified = false
'''
    )

    with pytest.raises(RegistryError, match="stage must be one of"):
        companies(tmp_path)


def test_hn_is_a_live_inline_registry_feed():
    """A Board carries a description instead of a company: HN postings name
    their own employer in each comment header, so a feed-level company would
    mislabel every one of them."""
    hn = next(entry for entry in entries() if entry.source == "hn")
    materialized = next(feed for feed in feeds() if feed.source == "hn")

    assert hn.key == "hn"  # defaulted from the source name
    assert hn.company_id is None
    assert hn.company_name is None
    assert hn.description.startswith("Hacker News")
    assert hn.verified
    assert materialized.key == "hn"
    assert materialized.description == hn.description


def test_every_source_declares_a_description():
    """Including ATS vendors, whose description is declared once in feeds.toml
    rather than repeated on each of their catalog feeds."""
    described = source_descriptions()

    assert set(described) == set(source_types())
    assert all(described.values())
    assert described["greenhouse"].startswith("Greenhouse")


def test_ats_feeds_inherit_their_source_description():
    ashby = [entry for entry in entries() if entry.source == "ashby"]

    assert len(ashby) > 1
    assert {entry.description for entry in ashby} == {source_descriptions()["ashby"]}


def test_source_taxonomy_comes_from_array_names():
    taxonomy = source_types()

    assert taxonomy["greenhouse"] == "ATS"
    assert taxonomy["amazon"] == "Direct"
    assert taxonomy["hn"] == "Board"


def test_a_tenantless_catalog_registers_its_source_without_seeding_feeds():
    """`paylocity` ships registered but empty, ahead of its first tenant. An
    empty catalog must validate, and must not be read as an orphan."""
    assert source_types()["paylocity"] == "ATS"
    by_source = [entry.source for entry in entries()]
    assert "paylocity" not in by_source
    assert "workday-list" in by_source


def test_catalog_feeds_use_the_canonical_field_order():
    documents = []
    for path in sorted((REGISTRY_DIR / "ats-catalog").glob("*.toml")):
        with path.open("rb") as handle:
            documents.extend(tomllib.load(handle).get("feed", []))

    assert documents
    for feed in documents:
        expected = _CATALOG_FIELD_ORDER + (("config",) if "config" in feed else ())
        assert tuple(feed) == expected


def test_inline_feeds_declare_only_runtime_fields_in_order():
    """Inline rows omit whatever the loader derives or defaults, so their key
    order is a subsequence of the canonical one rather than the whole thing."""
    with (REGISTRY_DIR / "feeds.toml").open("rb") as handle:
        registry = tomllib.load(handle)

    for table in ("direct", "board"):
        for source in registry.get(table, []):
            declared = tuple(source)
            assert declared[0] == "name"
            assert set(declared) <= {"name", *_INLINE_FIELD_ORDER}
            ranks = [_INLINE_FIELD_ORDER.index(key) for key in declared[1:]]
            assert ranks == sorted(ranks), f"{source['name']}: {declared}"
            # Company identity is derived from companies.toml, never declared.
            assert "company_name" not in source
            assert "domain" not in source
        assert registry.get(table)


def test_registry_rejects_retired_enabled_field(tmp_path):
    """``enabled`` moved to PostgreSQL; a stale catalog must fail loudly."""
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(_inline_row("direct", enabled="true"))

    with pytest.raises(RegistryError, match="unknown direct field 'enabled'"):
        entries(tmp_path)


def test_registry_rejects_catalogs_outside_registry(tmp_path):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[ats]]
name = "demo"
description = "Demo source."
catalog = "../outside.toml"
'''
    )

    with pytest.raises(RegistryError, match="must stay inside registry"):
        entries(tmp_path)


def test_registry_rejects_duplicate_names_across_source_types(tmp_path):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[ats]]
name = "demo"
description = "Demo source."
[[board]]
name = "demo"
description = "Demo source."
'''
    )

    with pytest.raises(RegistryError, match="duplicate source 'demo'"):
        entries(tmp_path)


@pytest.mark.parametrize("table", ["ats", "direct", "board"])
def test_registry_rejects_adapter_field(tmp_path, table):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        f'''
schema_version = 2
[[{table}]]
name = "demo"
description = "Demo source."
adapter = "json-api"
'''
    )

    with pytest.raises(RegistryError, match=f"unknown {table} field"):
        entries(tmp_path)


def test_registry_rejects_source_enabled_on_ats(tmp_path):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[ats]]
name = "demo"
description = "Demo source."
enabled = true
catalog = "ats-catalog/demo.toml"
'''
    )

    with pytest.raises(RegistryError, match="unknown ats field 'enabled'"):
        entries(tmp_path)


@pytest.mark.parametrize("table", ["direct", "board"])
def test_single_feed_fields_are_flattened(tmp_path, table):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(_inline_row(table))

    assert entries(tmp_path)[0].key == "demo-board"


def _write_companies_with_excluded(path):
    (path / "companies.toml").write_text(
        '''
schema_version = 2
[[company]]
id = 1
name = "Demo"
aliases = ["demo"]
domain = "unknown"
industry = "unknown"
desc = ""
verified = false

[[company]]
id = 2
name = "Retired"
aliases = ["retired"]
domain = "unknown"
industry = "unknown"
desc = ""
verified = false
excluded = true
'''
    )


def test_excluded_company_is_absent_from_companies(tmp_path):
    _write_companies_with_excluded(tmp_path)

    ids = {company.id for company in companies(tmp_path)}

    assert ids == {1}


def test_feed_referencing_excluded_company_is_skipped_not_rejected(tmp_path):
    _write_companies_with_excluded(tmp_path)
    (tmp_path / "feeds.toml").write_text(_inline_row("direct", company_id="2"))

    assert entries(tmp_path) == ()


def test_feed_referencing_unknown_company_still_rejected(tmp_path):
    _write_companies_with_excluded(tmp_path)
    (tmp_path / "feeds.toml").write_text(_inline_row("direct", company_id="999"))

    with pytest.raises(RegistryError, match="unknown company_id 999"):
        entries(tmp_path)


def test_registry_rejects_non_boolean_excluded(tmp_path):
    (tmp_path / "companies.toml").write_text(
        '''
schema_version = 2
[[company]]
id = 1
name = "Demo"
aliases = ["demo"]
domain = "unknown"
industry = "unknown"
desc = ""
verified = false
excluded = "yes"
'''
    )

    with pytest.raises(RegistryError, match="excluded must be a boolean"):
        companies(tmp_path)


def test_no_excluded_company_leaks_into_live_entries():
    """The registry retires 53 companies; only 4 of their catalogs carry a feed."""
    excluded_ids = {2155, 2474, 381, 695}  # recruitcrm, vincere, dayforce, bullhorn

    assert entries()
    assert not {entry.company_id for entry in entries()} & excluded_ids


@pytest.mark.parametrize("table", ["direct", "board"])
def test_registry_rejects_legacy_source_config_pointer(tmp_path, table):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        _inline_row(table, config='"../json-api-configs.py#demo"')
    )

    with pytest.raises(RegistryError, match="config must be a TOML table"):
        entries(tmp_path)
