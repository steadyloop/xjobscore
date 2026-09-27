"""Tests for launching and cancelling keyscan runs from the inspector."""

from __future__ import annotations

import sqlite3

import pytest

from xjobscore.inspector import keyscan_control


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "keyscan.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, methods_json TEXT NOT NULL, config_json TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, checkpoint_json TEXT NOT NULL DEFAULT '{}',
            counts_json TEXT NOT NULL DEFAULT '{}', artifact_dir TEXT NOT NULL,
            pid INTEGER, error TEXT, started_at TEXT NOT NULL,
            finished_at TEXT, blocked_json TEXT);
        CREATE TABLE tenants (
            ats TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',
            canonical_url TEXT NOT NULL, urls TEXT NOT NULL DEFAULT '[]', method TEXT,
            run_id TEXT, details TEXT NOT NULL DEFAULT '{}', discovered_at TEXT NOT NULL,
            checked_at TEXT, imported_at TEXT, PRIMARY KEY (ats, key));
        """
    )
    con.commit()
    con.close()
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_KEYSCAN_DB", str(path))
    return path


@pytest.fixture
def spawned(monkeypatch):
    """Capture the argv a launch would use without starting anything."""
    calls = []

    class FakeProcess:
        pid = 4242

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(keyscan_control.subprocess, "Popen", fake_popen)
    return calls


def _add_run(path, run_id, status, pid):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO runs(id, methods_json, config_json, status, phase, artifact_dir,"
        " pid, started_at) VALUES (?,'[\"commoncrawl\"]','{}',?,'x','/tmp',?,'t')",
        (run_id, status, pid),
    )
    con.commit()
    con.close()


def test_start_detaches_the_child_from_this_process(db, spawned):
    """The crawl must outlive an inspector restart, which is the whole point."""
    keyscan_control.start(date_from="2026-06-01", date_to="2026-08-21")

    _, kwargs = spawned[0]
    assert kwargs["start_new_session"] is True


def test_start_builds_argv_without_a_shell(db, spawned):
    keyscan_control.start(date_from="2026-06-01", date_to="2026-08-21", sources="tier1")

    argv, kwargs = spawned[0]
    assert argv[1:] == [
        "-m", "keyscan", "crawl", "--quiet",
        "--from", "2026-06-01", "--to", "2026-08-21", "--vendor", "tier1",
    ]
    assert "shell" not in kwargs


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-01", "2026-06-01; rm -rf /"])
def test_start_refuses_a_date_it_cannot_parse(db, spawned, bad):
    with pytest.raises(keyscan_control.LaunchError):
        keyscan_control.start(date_from=bad)

    assert spawned == []


def test_start_builds_argv_from_explicit_crawl_ids(db, spawned):
    keyscan_control.start(crawl_ids="CC-MAIN-2026-30,CC-MAIN-2026-25")

    argv, _ = spawned[0]
    assert argv[1:] == [
        "-m", "keyscan", "crawl", "--quiet",
        "CC-MAIN-2026-30,CC-MAIN-2026-25",
    ]


@pytest.mark.parametrize("bad", ["not-a-crawl", "CC-MAIN-2026-30; rm -rf /", "CC-MAIN-2026-30,nope"])
def test_start_refuses_a_crawl_id_it_cannot_parse(db, spawned, bad):
    with pytest.raises(keyscan_control.LaunchError):
        keyscan_control.start(crawl_ids=bad)

    assert spawned == []


def test_start_refuses_both_a_date_range_and_explicit_crawl_ids(db, spawned):
    with pytest.raises(keyscan_control.LaunchError):
        keyscan_control.start(date_from="2026-06-01", date_to="2026-08-21", crawl_ids="CC-MAIN-2026-30")

    assert spawned == []


@pytest.mark.parametrize("bad", ["haxx", "greenhouse; rm -rf /", "greenhouse,nope"])
def test_start_refuses_a_source_keyscan_does_not_define(db, spawned, bad):
    with pytest.raises(keyscan_control.LaunchError):
        keyscan_control.start(sources=bad)

    assert spawned == []


def test_start_accepts_every_name_keyscan_defines(db, spawned):
    keyscan_control.start(sources="greenhouse,tier1,ukg")

    assert spawned[0][0][-1] == "greenhouse,tier1,ukg"


def test_start_is_refused_while_a_live_run_holds_the_slot(db, spawned, monkeypatch):
    _add_run(db, "busy", "running", 1234)
    monkeypatch.setattr(keyscan_control, "process_alive", lambda pid: True)

    with pytest.raises(keyscan_control.RunAlreadyActive):
        keyscan_control.start()

    assert spawned == []


def test_a_running_row_whose_process_died_does_not_block_forever(db, spawned, monkeypatch):
    """kill -9 or a power cut leaves `running` behind; that is wreckage."""
    _add_run(db, "stale", "running", 999999)
    monkeypatch.setattr(keyscan_control, "process_alive", lambda pid: False)

    keyscan_control.start()

    assert len(spawned) == 1


def test_process_alive_is_false_for_a_pid_that_is_gone():
    assert keyscan_control.process_alive(None) is False
    assert keyscan_control.process_alive(0) is False
    # Above the usual pid_max, so it cannot be a live process.
    assert keyscan_control.process_alive(4_294_967_294) is False


def test_process_alive_is_true_for_this_process():
    import os

    assert keyscan_control.process_alive(os.getpid()) is True
