"""CLI: `PYTHONPATH=. python -m xjobscore.raw_inspection [source ...]`.

Flags over environment over defaults, and `async def main(argv) -> int` under a
single `asyncio.run`, both following run.py. Progress goes to stderr so stdout
stays a clean summary you can pipe.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from xjobscore import sources
from xjobscore.raw_inspection import DEFAULT_HARVESTED, DEFAULT_PARSED, inspect_all, targets


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m xjobscore.raw_inspection",
        description=(
            "Harvest each source adapter once against a live catalog feed, write the "
            "envelope plus the first item to <harvested-out>/<source>.json, then run "
            "that source's parser on it and write <parsed-out>/<source>.json."
        ),
    )
    parser.add_argument("sources", nargs="*", help="restrict to these sources (default: all)")
    parser.add_argument(
        "--harvested-out", type=Path, default=DEFAULT_HARVESTED, help=f"default: {DEFAULT_HARVESTED}"
    )
    parser.add_argument(
        "--parsed-out", type=Path, default=DEFAULT_PARSED, help=f"default: {DEFAULT_PARSED}"
    )
    parser.add_argument(
        "--harvest-only", action="store_true", help="skip the parse pass entirely"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per source")
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("XJOBSCORE_USER_AGENT") or "xjobscore/0.1",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the resolved targets and exit without fetching",
    )
    return parser.parse_args(argv)


def _report(result: dict) -> None:
    source = result["source"]
    if "skipped" in result:
        print(f"skip  {source:<18} {result['skipped']}", file=sys.stderr)
    elif result["ok"]:
        count = result["item_count"]
        empty = "  <no items>" if count == 0 else ""
        print(
            f"ok    {source:<18} items={count:<6} http={result['http_status']}"
            f"  {result['elapsed_s']}s{empty}",
            file=sys.stderr,
        )
    else:
        error = result["error"]
        print(
            f"ERR   {source:<18} {error['class']}: {error['detail'][:90]}"
            f"  {result['elapsed_s']}s",
            file=sys.stderr,
        )


async def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    unknown = sorted(set(args.sources) - set(sources.SOURCES))
    if unknown:
        print(f"unknown source(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"known: {', '.join(sorted(sources.SOURCES))}", file=sys.stderr)
        return 2

    plan = targets(args.sources or None)
    if not plan:
        print("nothing to do", file=sys.stderr)
        return 2

    if args.list:
        for target in plan:
            if target.skip is not None:
                print(f"{target.source:<18} SKIP  {target.skip}")
            else:
                feed = target.feed
                print(f"{target.source:<18} {feed.key:<44} {feed.company_name or ''}")
        return 0

    parsed_dir = None if args.harvest_only else args.parsed_out
    print(f"-> {args.harvested_out}", file=sys.stderr)
    if parsed_dir is not None:
        print(f"-> {parsed_dir}", file=sys.stderr)
    results = await inspect_all(
        plan,
        harvested_dir=args.harvested_out,
        parsed_dir=parsed_dir,
        user_agent=args.user_agent,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
        on_done=_report,
    )

    ok = [r for r in results if r["ok"]]
    skipped = [r for r in results if "skipped" in r]
    failed = [r for r in results if not r["ok"] and "skipped" not in r]
    empty = [r for r in ok if r["item_count"] == 0]

    print(
        f"{len(ok)} ok ({len(empty)} with no items), "
        f"{len(failed)} error, {len(skipped)} skipped, {len(results)} total"
    )
    if failed:
        print("errors: " + ", ".join(sorted(r["source"] for r in failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except KeyboardInterrupt:
        sys.exit(130)
