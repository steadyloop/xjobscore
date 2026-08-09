"""``python -m oneshot_ui.demo <seed|reset|verify|path>``.

The package is not installed and there is no ``xjobscore/__init__.py``, so
``xjobscore.*`` is importable only when the repository root is on ``sys.path``.
``run.py`` handles that for the server; this module does the same for the CLI,
which is why the path fix runs before anything else is imported.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]  # .../xjobscore/oneshot-ui
REPOSITORY_ROOT = APP_ROOT.parents[1]
for _path in (REPOSITORY_ROOT, APP_ROOT):
    _value = str(_path)
    if _value not in sys.path:
        sys.path.insert(0, _value)

import argparse  # noqa: E402
import asyncio  # noqa: E402

from ..config import load_settings  # noqa: E402


def _database_path(argument: str | None) -> Path:
    if argument:
        return Path(argument).expanduser()
    return load_settings(demo=True).database_path


async def _seed(path: Path, *, fresh: bool) -> int:
    # Imported here so 'verify' and 'path' do not pay for the fetch stack.
    from .seeder import build_coordinator, seed, seed_fresh, summarize

    if fresh:
        report = await seed_fresh(path)
    else:
        from ..db import Database

        database = Database(path)
        coordinator = build_coordinator(database)
        await coordinator.initialize()
        try:
            report = await seed(database, coordinator)
        finally:
            await coordinator.close()
    print(f"seeded {path}")
    print(summarize(report))
    return 0


async def _verify(path: Path) -> int:
    from ..db import Database
    from .audit import verify

    if not path.exists():
        print(f"no demo database at {path}; run 'seed' first", file=sys.stderr)
        return 2
    database = Database(path)
    try:
        report = await verify(database)
    finally:
        await database.close()
    print(report.render())
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m oneshot_ui.demo")
    parser.add_argument(
        "command",
        choices=("seed", "reset", "verify", "path"),
        help=(
            "seed: fill the demo database (no-op fields are still rewritten); "
            "reset: delete it and seed from scratch; "
            "verify: report empty tables and all-NULL columns; "
            "path: print the demo database path"
        ),
    )
    parser.add_argument("--db", default=None, help="override the demo database path")
    args = parser.parse_args(argv)
    path = _database_path(args.db)

    if args.command == "path":
        print(path)
        return 0
    if args.command == "reset":
        return asyncio.run(_seed(path, fresh=True))
    if args.command == "seed":
        return asyncio.run(_seed(path, fresh=not path.exists()))
    return asyncio.run(_verify(path))


if __name__ == "__main__":
    raise SystemExit(main())
