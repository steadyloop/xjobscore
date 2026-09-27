"""Tests for the KeyScan page's read-only views over keyscan's SQLite."""

from __future__ import annotations

import json
import sqlite3

import pytest

from xjobscore.inspector import keyscan_view


def _make_db(path, *, tokens=(), runs=()):
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
        CREATE TABLE footers (
            crawl TEXT NOT NULL, part TEXT NOT NULL, rg INTEGER NOT NULL,
            lo TEXT NOT NULL, hi TEXT NOT NULL, bytes INTEGER NOT NULL,
            harvested_at TEXT NOT NULL, PRIMARY KEY (crawl, part, rg));
        CREATE TABLE scanned (
            ats TEXT NOT NULL, crawl TEXT NOT NULL, scanned_at TEXT NOT NULL,
            PRIMARY KEY (ats, crawl));
        """
    )
    con.executemany(
        "INSERT INTO runs(id, methods_json, config_json, status, phase, checkpoint_json,"
        " counts_json, artifact_dir, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
        runs,
    )
    con.executemany(
        "INSERT INTO tenants(ats, key, status, canonical_url, method, details,"
        " discovered_at, checked_at, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
        tokens,
    )
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "keyscan.sqlite"
    _make_db(
        path,
        runs=[
            ("old", '["board"]', "{}", "completed", "board", "{}",
             '{"found": 3}', str(tmp_path / "old"), "2026-01-01"),
            ("live", '["commoncrawl"]', '{"date_from": "2026-06-01"}', "running", "commoncrawl",
             '{"files": 50, "total_files": 100}', "{}", str(tmp_path / "live"),
             "2026-02-01"),
        ],
        tokens=[
            ("greenhouse", "alpha", "live", "https://x/alpha", "cc",
             json.dumps({"url_count": 40, "job_count": 7}), "2026-02-02", "2026-02-02", None),
            ("greenhouse", "beta", "dead", "https://x/beta", "cc",
             json.dumps({"url_count": 1}), "2026-02-01", "2026-02-01", None),
            ("lever", "gamma", "live", "https://y/gamma", "board",
             "{}", "2026-02-03", None, "2026-02-04"),
        ],
    )
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_KEYSCAN_DB", str(path))
    return path


def test_missing_database_is_reported_not_created(tmp_path, monkeypatch):
    """A laptop that has never scanned is not a broken inspector."""
    missing = tmp_path / "nope.sqlite"
    monkeypatch.setenv("XJOBSCORE_INSPECTOR_KEYSCAN_DB", str(missing))

    with pytest.raises(keyscan_view.DatabaseMissing):
        keyscan_view.tally()

    assert not missing.exists()


def test_status_prefers_the_running_run_over_the_newest(db):
    row = keyscan_view.status()

    assert row["id"] == "live"
    # The *_json columns are decoded, not handed to the page as strings.
    assert row["checkpoint"] == {"files": 50, "total_files": 100}
    assert row["methods"] == ["commoncrawl"]


def test_found_is_newest_first_and_lifts_url_count_out_of_details(db):
    rows = keyscan_view.found(limit=10)

    assert [row["key"] for row in rows] == ["gamma", "alpha", "beta"]
    assert rows[1]["url_count"] == 40


def test_candidates_filters_and_exposes_both_detail_counts(db):
    rows = keyscan_view.candidates(status_filter="live")

    assert {row["key"] for row in rows} == {"alpha", "gamma"}
    alpha = next(row for row in rows if row["key"] == "alpha")
    assert (alpha["url_count"], alpha["job_count"]) == (40, 7)


def test_candidates_source_filter_binds_rather_than_interpolates(db):
    assert keyscan_view.candidates(source="lever'; DROP TABLE token; --") == []
    # The table is still there, which a string-interpolated query would not
    # guarantee.
    assert keyscan_view.tally()["found"] == 3


def test_tally_counts_pending_the_way_the_import_bridge_selects(db):
    """Pending is live *or* dead and never imported -- keyscan_import's own
    predicate. Counting only live understates what the button will write."""
    tally = keyscan_view.tally()

    assert tally == {
        "found": 3, "live": 2, "dead": 1, "unknown": 0,
        "imported": 1, "pending": 2,
    }


def test_pending_filter_matches_the_pending_count(db):
    rows = keyscan_view.candidates(imported="pending")

    assert {row["key"] for row in rows} == {"alpha", "beta"}
    assert keyscan_view.candidate_count(imported="pending") == len(rows)


def test_imported_filter_is_the_complement(db):
    rows = keyscan_view.candidates(imported="imported")

    assert [row["key"] for row in rows] == ["gamma"]


def test_offset_pages_without_repeating_a_row(db):
    first = keyscan_view.candidates(limit=2, offset=0)
    second = keyscan_view.candidates(limit=2, offset=2)

    assert len(first) == 2
    assert {row["key"] for row in first}.isdisjoint({row["key"] for row in second})


def test_run_log_reads_from_the_offset_and_reports_the_new_one(db, tmp_path):
    artifact = tmp_path / "live"
    artifact.mkdir()
    (artifact / "run.jsonl").write_text(
        "\n".join(json.dumps({"at": "t", "message": f"line {i}"}) for i in range(5)),
        encoding="utf-8",
    )

    first = keyscan_view.run_log("live", offset=0)
    assert len(first["entries"]) == 5
    assert first["offset"] == 5

    # A second poll from that offset sees nothing new.
    assert keyscan_view.run_log("live", offset=first["offset"])["entries"] == []


def test_run_log_for_a_run_with_no_artifact_is_empty_not_an_error(db):
    assert keyscan_view.run_log("old")["missing"] is True


def test_run_log_skips_a_half_written_final_line(db, tmp_path):
    artifact = tmp_path / "live"
    artifact.mkdir()
    (artifact / "run.jsonl").write_text(
        json.dumps({"message": "complete"}) + "\n" + '{"message": "trunc',
        encoding="utf-8",
    )

    entries = keyscan_view.run_log("live")["entries"]

    assert [entry["message"] for entry in entries] == ["complete"]


def _add_footers(rows):
    con = sqlite3.connect(keyscan_view.database_path())
    con.executemany("INSERT INTO footers VALUES (?,?,?,?,?,?,'t')", rows)
    con.commit()
    con.close()


def test_coverage_decides_every_cell_from_footers_alone(db):
    """One crawl: greenhouse overlaps a row group, lever provably does not."""
    _add_footers([
        # Spans io,github .. io,medium -- greenhouse's band sits inside.
        ("CC-MAIN-2026-34", "subset=warc/part-00000", 0, "io,github)/a", "io,medium)/z", 111),
        # Far side of the key space; overlaps no vendor here.
        ("CC-MAIN-2026-34", "subset=warc/part-00001", 0, "zz,aaa)/a", "zz,zzz)/z", 55),
    ])

    result = keyscan_view.coverage()

    assert [row["crawl"] for row in result["crawls"]] == ["CC-MAIN-2026-34"]
    cells = result["crawls"][0]["cells"]
    assert cells["greenhouse"] == {
        "state": "pending", "row_groups": 1, "files": 1, "bytes": 111,
    }
    assert cells["lever"] == {"state": "none"}
    # Two files is nowhere near a complete 600-file harvest.
    assert result["crawls"][0]["partial"] is True


def test_coverage_reports_a_scanned_pair_instead_of_recomputing_it(db):
    _add_footers([
        ("CC-MAIN-2026-34", "subset=warc/part-00000", 0, "io,github)/a", "io,medium)/z", 111),
    ])
    con = sqlite3.connect(keyscan_view.database_path())
    con.execute("INSERT INTO scanned VALUES ('greenhouse', 'CC-MAIN-2026-34', '2026-09-02')")
    con.commit()
    con.close()

    cells = keyscan_view.coverage()["crawls"][0]["cells"]

    assert cells["greenhouse"] == {"state": "scanned", "at": "2026-09-02"}
