from __future__ import annotations

import tomllib

import pytest

from xjobscore.sources.registry.loader import (
    REGISTRY_DIR,
    RegistryError,
    companies,
    entries,
    feeds,
    source_types,
)


_FEED_FIELD_ORDER = (
    "key",
    "verified",
    "verified_at",
    "company_id",
    "company_name",
    "domain",
)


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
    company_ids = {company.id for company in companies()}

    assert company_ids
    assert {entry.company_id for entry in entries()} <= company_ids


def test_company_aliases_are_flat_norm_lists_seeded_with_slug():
    ambient = next(company for company in companies() if company.id == 97)

    assert ambient.aliases == ("ambient-ai", "ambient.ai")
    assert all(isinstance(alias_norm, str) for company in companies() for alias_norm in company.aliases)


def test_company_industries_are_explicit():
    assert all(company.industry for company in companies())


def test_hn_is_a_live_inline_registry_feed():
    hn = next(entry for entry in entries() if entry.source == "hn")
    materialized = next(feed for feed in feeds() if feed.source == "hn")

    assert hn.key == "hn"
    assert hn.company_id == 2463  # Y Combinator; the Hacker News entry was merged into it
    assert hn.company_name == "Hacker News"
    assert hn.domain == "news.ycombinator.com"
    assert hn.verified
    assert hn.verified_at == "2026-08-09"
    assert materialized.key == "hn"
    assert materialized.company_name == "Hacker News"


def test_source_taxonomy_comes_from_array_names():
    taxonomy = source_types()

    assert taxonomy["greenhouse"] == "ATS"
    assert taxonomy["amazon"] == "Direct"
    assert taxonomy["hn"] == "Board"


def test_registry_feeds_use_the_canonical_field_order():
    documents = []
    for path in sorted((REGISTRY_DIR / "ats-catalog").glob("*.toml")):
        with path.open("rb") as handle:
            documents.extend(tomllib.load(handle).get("feed", []))
    with (REGISTRY_DIR / "feeds.toml").open("rb") as handle:
        registry = tomllib.load(handle)
    documents.extend(
        {key: value for key, value in source.items() if key != "name"}
        for table in ("direct", "board")
        for source in registry.get(table, [])
    )

    assert documents
    for feed in documents:
        expected = _FEED_FIELD_ORDER + (("config",) if "config" in feed else ())
        assert tuple(feed) == expected


def test_registry_rejects_retired_enabled_field(tmp_path):
    """``enabled`` moved to PostgreSQL; a stale catalog must fail loudly."""
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[direct]]
name = "demo"
key = "demo"
company_id = 1
company_name = "Demo"
domain = "example.com"
verified = true
verified_at = "2026-08-10"
enabled = true
'''
    )

    with pytest.raises(RegistryError, match="unknown direct field 'enabled'"):
        entries(tmp_path)


def test_registry_rejects_catalogs_outside_registry(tmp_path):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[ats]]
name = "demo"
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
[[board]]
name = "demo"
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
enabled = true
catalog = "ats-catalog/demo.toml"
'''
    )

    with pytest.raises(RegistryError, match="unknown ats field 'enabled'"):
        entries(tmp_path)


@pytest.mark.parametrize("table", ["direct", "board"])
def test_single_feed_fields_are_flattened(tmp_path, table):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        '''
schema_version = 2
[[{table}]]
name = "demo"
key = "demo-board"
company_id = 1
company_name = "Demo"
domain = "example.com"
verified = true
verified_at = "2026-08-10"
'''.format(table=table)
    )

    assert entries(tmp_path)[0].key == "demo-board"


@pytest.mark.parametrize("table", ["direct", "board"])
def test_registry_rejects_legacy_source_config_pointer(tmp_path, table):
    _write_companies(tmp_path)
    (tmp_path / "feeds.toml").write_text(
        f'''
schema_version = 2
[[{table}]]
name = "demo"
key = "demo"
company_id = 1
company_name = "Demo"
domain = "example.com"
verified = true
verified_at = "2026-08-10"
config = "../json-api-configs.py#demo"
'''
    )

    with pytest.raises(RegistryError, match="config must be a TOML table"):
        entries(tmp_path)
