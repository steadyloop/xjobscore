"""Command line interface for the standalone keyscan subsystem."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .core import TARGET_MBPS, ActiveRunError, Keyscan, KeyscanError, selftest
from .rules import SAMPLES
from .verify import verify_sources


def _methods(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _print_verify_table(results: list[dict]) -> None:
    header = f"{'vendor':<17}{'outcome':<16}{'live':<6}{'dead':<6}{'synthetic':<14}{'discrimination':<22}{'probes':>6}"
    print(header)
    print("-" * len(header))
    for result in results:
        live = "✓" if result.get("live") else ("-" if "live" in result else "")
        dead = "✓" if result.get("dead") else ("-" if "dead" in result else "")
        synthetic = (result.get("synthetic") or {}).get("status", "-")
        discrimination = (result.get("discrimination") or "-").replace("_", " ")
        probes = result.get("probes", 0)
        print(f"{result['source']:<17}{result['outcome']:<16}{live:<6}{dead:<6}{synthetic:<14}{discrimination:<22}{probes:>6}")
        if result.get("detail"):
            print(f"  {result['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="keyscan", description="Standalone ATS token discovery and validation"
    )
    parser.add_argument("--workdir", type=Path, help="default: keyscan/work")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="check Python and DuckDB extraction rules")
    run = sub.add_parser("run", help="start an explicit discovery run")
    run.add_argument("--methods", required=True, help="comma-separated: company,commoncrawl,wayback,board,validate")
    run.add_argument("--sources", help="comma-separated ATS sources or group names (e.g. tier1); default: all")
    run.add_argument("--crawls", type=int, default=6, help="Common Crawl snapshots, 1-12 (default: 6)")
    run.add_argument(
        "--archives", action="store_true",
        help="board method only: also read the boards' frozen archived/*.json history",
    )
    run.add_argument(
        "--target-mbps", type=float, default=TARGET_MBPS,
        help=f"average Common Crawl transfer ceiling (default: {TARGET_MBPS:g}); 0 disables pacing",
    )
    verify = sub.add_parser(
        "verify", help="sample-verify vendor rules against live data without writing to the token table"
    )
    verify.add_argument("--sources", required=True, help="comma-separated ATS sources or group names (e.g. tier1)")
    verify.add_argument("--crawls", type=int, default=1, help="Common Crawl snapshots to scan (default: 1)")
    verify.add_argument("--max-probes", type=int, default=40, help="probe cap per vendor before giving up (default: 40)")
    verify.add_argument("--json", action="store_true", help="print raw JSON instead of a summary table")
    robots = sub.add_parser("robots", help="report shared ATS hosts that block CCBot")
    robots.add_argument("--sources", help="comma-separated ATS sources; default: all")
    resume = sub.add_parser("resume", help="resume an interrupted run")
    resume.add_argument("run_id")
    status = sub.add_parser("status", help="show the latest or named run")
    status.add_argument("run_id", nargs="?")
    cancel = sub.add_parser("cancel", help="request cancellation of a run")
    cancel.add_argument("run_id")
    candidates = sub.add_parser("candidates", help="list discovered candidates")
    candidates.add_argument("--status", choices=("unknown", "live", "dead"))
    candidates.add_argument("--source")
    candidates.add_argument("--query", default="")
    candidates.add_argument("--limit", type=int, default=100)
    logs = sub.add_parser("logs", help="read a run's durable log")
    logs.add_argument("run_id")
    logs.add_argument("--offset", type=int, default=0)
    logs.add_argument("--limit", type=int, default=200)

    args = parser.parse_args()
    if args.command == "selftest":
        failures = selftest()
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
        print(f"{len(SAMPLES)}/{len(SAMPLES)} extraction fixtures pass in Python and DuckDB")
        return 0

    scan = Keyscan(args.workdir, target_mbps=getattr(args, "target_mbps", TARGET_MBPS))
    try:
        if args.command == "run":
            run_id = asyncio.run(
                scan.run(_methods(args.methods), args.crawls, _methods(args.sources) or None, args.archives)
            )
            _print(scan.status(run_id))
        elif args.command == "verify":
            results = asyncio.run(
                verify_sources(
                    tuple(_methods(args.sources)), crawls=args.crawls,
                    max_probes=args.max_probes, workdir=scan.workdir,
                )
            )
            if args.json:
                _print(results)
            else:
                _print_verify_table(results)
            return 1 if any(r.get("discrimination") == "fail_synthetic_live" for r in results) else 0
        elif args.command == "robots":
            _print(asyncio.run(scan.robots(_methods(args.sources) or None)))
        elif args.command == "resume":
            run_id = asyncio.run(scan.resume(args.run_id))
            _print(scan.status(run_id))
        elif args.command == "status":
            current = scan.status(args.run_id)
            if current is None:
                print("no keyscan runs", file=sys.stderr)
                return 1
            _print(current)
        elif args.command == "cancel":
            scan.cancel(args.run_id)
            _print(scan.status(args.run_id))
        elif args.command == "candidates":
            _print(scan.list_candidates(status=args.status, source=args.source, query=args.query, limit=args.limit))
        elif args.command == "logs":
            entries, offset = scan.tail_log(args.run_id, args.offset, args.limit)
            _print({"entries": entries, "next_offset": offset})
    except KeyboardInterrupt:
        print("interrupted; run `resume` to continue", file=sys.stderr)
        return 130
    except (KeyscanError, ActiveRunError) as exc:
        print(f"keyscan: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
