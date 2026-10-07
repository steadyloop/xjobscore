"""Starting and cancelling keyscan runs from the KeyScan page.

The inspector does not *run* a crawl. It asks the OS to start one and then
goes back to reading the database that crawl writes -- ``keyscan_view`` is
still the whole of the page's data path.

That distinction is the point of ``start_new_session=True`` below: the child
gets its own session and process group, so it is not a child of uvicorn in any
way that matters. Restarting the inspector, reloading it on a file save, or
closing the browser leaves a three-hour crawl running. The alternative --
parenting the crawl to the web server -- means every deploy interrupts one.

Cancellation needs no signal at all. ``Keyscan._check_cancelled`` re-reads the
run's own row from SQLite at every checkpoint and stops when it is no longer
``running`` (core.py), so writing that row *is* the cancel. This is what
``keyscan cancel`` does too, and it avoids the usual cross-process trap of
signalling a PID that a reboot has since handed to something else.

Two invariants keep this honest:

* keyscan owns its bookkeeping. It inserts its own ``runs`` row, records its
  own pid, and its ``keyscan_one_active`` unique index -- not anything here --
  is what makes "one run at a time" true.
* Nothing user-typed reaches the argv. Dates are parsed, and sources must name
  a real rule or group, or the launch is refused.
"""

from __future__ import annotations

from datetime import date
import os
import re
from pathlib import Path
import subprocess
import sys
from typing import Any

from keyscan.rules import BY_SOURCE, SOURCE_GROUPS

from xjobscore.inspector import keyscan_view

__all__ = ["LaunchError", "RunAlreadyActive", "cancel", "process_alive", "start"]

# `python -m keyscan` needs the repository root importable, the same way the
# README's invocation does.
REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_SOURCES = frozenset(BY_SOURCE) | frozenset(SOURCE_GROUPS)

# Mirrors keyscan.core.CRAWL_ID_RE, duplicated rather than imported: that
# module pulls in duckdb, which this read-mostly page should not pay for just
# to validate a launch argument's shape.
_CRAWL_ID_RE = re.compile(r"^CC-MAIN-\d{4}-\d{2}$")


class LaunchError(RuntimeError):
    """The requested run was rejected before anything was started."""


class RunAlreadyActive(LaunchError):
    """keyscan allows one run at a time; one is already going."""


def _checked_date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise LaunchError(f"{field} is not a YYYY-MM-DD date: {value!r}") from exc


def _checked_crawl_ids(value: str | None) -> str | None:
    """Accept only well-formed Common Crawl snapshot ids, never free text."""
    if not value or not value.strip():
        return None
    ids = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in ids if not _CRAWL_ID_RE.fullmatch(item)]
    if invalid:
        raise LaunchError(f"invalid crawl id(s): {', '.join(invalid)}")
    return ",".join(ids)


def _checked_sources(value: str | None) -> str | None:
    """Accept only names keyscan itself defines, never free text."""
    if not value or not value.strip():
        return None
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in VALID_SOURCES]
    if unknown:
        raise LaunchError(f"unknown source(s): {', '.join(sorted(unknown))}")
    return ",".join(names)


def process_alive(pid: int | None) -> bool:
    """Whether a recorded pid is still a running process on this machine.

    A run row can say ``running`` after its process died -- a kill -9, a power
    cut -- and that row then blocks the next launch. This is how the page tells
    a live run from that wreckage.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by another user.
        return True
    except (TypeError, ValueError, OverflowError, OSError):
        # OverflowError is os.kill's answer to a pid too large for the
        # platform -- a corrupt row must not take the status endpoint down.
        return False
    return True


def active_run() -> dict[str, Any] | None:
    """The genuinely-running run, if there is one.

    A ``running`` row whose process is gone is reported as stale rather than
    active, so it does not block a new launch forever.
    """
    run = keyscan_view.status()
    if run is None or run.get("status") != "running":
        return None
    return run


def start(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    crawl_ids: str | None = None,
    sources: str | None = None,
    validate: bool = False,
    rescan: bool = False,
) -> dict[str, Any]:
    """Launch a detached ``keyscan crawl`` and return immediately.

    The returned dict describes the *launch*, not the run: keyscan writes its
    own ``runs`` row a moment later, and the page picks that up on its next
    poll like any other run.
    """
    if crawl_ids and (date_from or date_to):
        raise LaunchError("use a date range or explicit crawl ids, not both")

    database = keyscan_view.database_path()
    if not database.exists():
        raise LaunchError(f"no keyscan database at {database}")

    running = active_run()
    if running is not None and process_alive(running.get("pid")):
        raise RunAlreadyActive(f"run {running['id']} is already running")

    argv = [sys.executable, "-m", "keyscan", "crawl", "--quiet"]
    checked_crawl_ids = _checked_crawl_ids(crawl_ids)
    if checked_crawl_ids:
        argv.append(checked_crawl_ids)
    for flag, value in (
        ("--from", _checked_date(date_from, "--from")),
        ("--to", _checked_date(date_to, "--to")),
        ("--vendor", _checked_sources(sources)),
    ):
        if value:
            argv += [flag, value]
    if validate:
        argv.append("--validate")
    if rescan:
        argv.append("--rescan")

    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    # stdout carries the final JSON result and stderr the durable-log echo;
    # both are kept so a launch that dies before writing a `runs` row is still
    # diagnosable. run.jsonl remains the real log.
    log_path = database.parent / "launch.log"
    try:
        log = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        raise LaunchError(f"cannot open {log_path}: {exc}") from exc
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is built here, never shell
            argv,
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            # The whole point: its own session, so it outlives this server.
            start_new_session=True,
        )
    except OSError as exc:
        raise LaunchError(f"could not start keyscan: {exc}") from exc
    finally:
        log.close()
    return {"pid": process.pid, "argv": argv[1:], "log": str(log_path)}


def cancel(run_id: str) -> dict[str, Any]:
    """Ask a running scan to stop, by writing the row it polls.

    Uses keyscan's own ``cancel`` rather than a signal: the run re-reads its
    status at every checkpoint, so this stops it at a consistent point with
    its checkpoint intact, and a resume picks up from there.
    """
    # Imported here, not at module scope: `keyscan.core` pulls in duckdb, and
    # the read-only page should not pay for that on every inspector start.
    from keyscan.core import Keyscan, KeyscanError

    scan = Keyscan(keyscan_view.database_path().parent)
    try:
        scan.cancel(run_id)
    except KeyscanError as exc:
        raise LaunchError(str(exc)) from exc
    return {"cancelled": run_id}
