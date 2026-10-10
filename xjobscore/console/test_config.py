"""Configuration upgrades keep existing deployments on their chosen storage."""

from pathlib import Path

import pytest

from xjobscore.console import config, keyscan_view


@pytest.fixture(autouse=True)
def clear_console_settings(monkeypatch):
    for prefix in ("XJOBSCORE_CONSOLE_", "XJOBSCORE_INSPECTOR_"):
        for suffix in ("STORE_DSN", "SCHEMA", "KEYSCAN_DB"):
            monkeypatch.delenv(prefix + suffix, raising=False)


def test_storage_defaults_to_console_schema_and_pipeline_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/pipeline")
    assert config.persistence_schema() == "console"
    assert config.persistence_dsn() == "postgresql://test/pipeline"


@pytest.mark.parametrize(
    ("suffix", "read", "old", "new", "expected"),
    [
        ("STORE_DSN", config.persistence_dsn, "postgresql://old/db", "postgresql://new/db", "postgresql://new/db"),
        ("SCHEMA", config.persistence_schema, "inspector", "custom_console", "custom_console"),
        ("KEYSCAN_DB", keyscan_view.database_path, "/old/keyscan.sqlite", "/new/keyscan.sqlite", Path("/new/keyscan.sqlite")),
    ],
)
def test_new_setting_takes_precedence(monkeypatch, suffix, read, old, new, expected):
    monkeypatch.setenv(f"XJOBSCORE_INSPECTOR_{suffix}", old)
    monkeypatch.setenv(f"XJOBSCORE_CONSOLE_{suffix}", new)
    assert read() == expected


@pytest.mark.parametrize(
    ("suffix", "read", "value", "expected"),
    [
        ("STORE_DSN", config.persistence_dsn, "postgresql://old/db", "postgresql://old/db"),
        ("SCHEMA", config.persistence_schema, "inspector", "inspector"),
        ("KEYSCAN_DB", keyscan_view.database_path, "/old/keyscan.sqlite", Path("/old/keyscan.sqlite")),
    ],
)
def test_old_setting_remains_a_deprecated_alias(monkeypatch, suffix, read, value, expected):
    monkeypatch.setenv(f"XJOBSCORE_INSPECTOR_{suffix}", value)
    with pytest.warns(DeprecationWarning, match=f"use XJOBSCORE_CONSOLE_{suffix}"):
        assert read() == expected


@pytest.mark.parametrize("schema", ["public", "", "BadSchema", "has-dash", "console; DROP SCHEMA public"])
def test_schema_override_rejects_unsafe_names(monkeypatch, schema):
    monkeypatch.setenv("XJOBSCORE_CONSOLE_SCHEMA", schema)
    with pytest.raises(RuntimeError, match="XJOBSCORE_CONSOLE_SCHEMA"):
        config.persistence_schema()
