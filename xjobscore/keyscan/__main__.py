"""Command line interface for the standalone keyscan subsystem."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .core import ActiveRunError, REGISTRY_CATALOG_DIR, Keyscan, KeyscanError, selftest
from .rules import SAMPLES


def _methods(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="keyscan", description="Standalone ATS token discovery and validation"
    )
    parser.add_argument("--workdir", type=Path, help="default: keyscan/work")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="check Python and DuckDB extraction rules")
    run = sub.add_parser("run", help="start an explicit discovery run")
    run.add_argument("--methods", required=True, help="comma-separated: company,commoncrawl,wayback,validate")
    run.add_argument("--sources", help="comma-separated ATS sources; default: all")
    run.add_argument("--crawls", type=int, default=6, help="Common Crawl snapshots, 1-12 (default: 6)")
    robots = sub.add_parser("robots", help="report shared ATS hosts that block CCBot")
    robots.add_argument("--sources", help="comma-separated ATS sources; default: all")
    serve = sub.add_parser("serve", help="run the HTTP daemon for API-triggered runs and polling")
    serve.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1 (no auth; keep it local)")
    serve.add_argument("--port", type=int, default=8787)
    resume = sub.add_parser("resume", help="resume an interrupted run")
    resume.add_argument("run_id")
    status = sub.add_parser("status", help="show the latest or named run")
    status.add_argument("run_id", nargs="?")
    cancel = sub.add_parser("cancel", help="request cancellation of a run")
    cancel.add_argument("run_id")
    candidates = sub.add_parser("candidates", help="list discovered candidates")
    candidates.add_argument("--validation", choices=("unvalidated", "live", "absent", "inconclusive"))
    candidates.add_argument("--decision", choices=("pending", "accepted", "rejected"))
    candidates.add_argument("--source")
    candidates.add_argument("--query", default="")
    candidates.add_argument("--limit", type=int, default=100)
    decide = sub.add_parser("accept", help="accept a live candidate")
    decide.add_argument("candidate_id", type=int)
    reject = sub.add_parser("reject", help="reject a candidate")
    reject.add_argument("candidate_id", type=int)
    export = sub.add_parser("export", help="export candidates as JSONL")
    export.add_argument("path", type=Path)
    export.add_argument("--accepted", action="store_true", help="export accepted candidates only")
    live_toml = sub.add_parser("export-live-toml", help="export every live token as a standalone TOML snapshot")
    live_toml.add_argument("path", type=Path)
    catalog = sub.add_parser("catalog", help="update registry ATS catalog TOML files")
    catalog.add_argument("--accepted", action="store_true", help="write accepted live candidates only")
    catalog.add_argument("--candidates", action="store_true", help="write all discovered candidates as disabled, unverified entries")
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
    if args.command == "serve":
        # Imported here so the blocking CLI never pays for FastAPI/uvicorn.
        from .server import serve as run_server

        run_server(args.workdir, args.host, args.port)
        return 0

    scan = Keyscan(args.workdir)
    try:
        if args.command == "run":
            run_id = asyncio.run(
                scan.run(_methods(args.methods), args.crawls, _methods(args.sources) or None)
            )
            _print(scan.status(run_id))
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
            _print(scan.list_candidates(validation=args.validation, decision=args.decision, source=args.source, query=args.query, limit=args.limit))
        elif args.command == "accept":
            _print(scan.decide(args.candidate_id, "accepted"))
        elif args.command == "reject":
            _print(scan.decide(args.candidate_id, "rejected"))
        elif args.command == "export":
            count = scan.export(args.path, accepted_only=args.accepted)
            print(f"exported {count} candidates to {args.path}")
        elif args.command == "export-live-toml":
            count = scan.export_live_toml(args.path)
            print(f"exported {count} live tokens to {args.path}")
        elif args.command == "catalog":
            result = scan.export_catalog(accepted_only=args.accepted, candidates=args.candidates)
            print(f"updated {result['feeds']} feeds across {result['files']} TOML files in {REGISTRY_CATALOG_DIR}")
            if result.get("skipped"):
                print(f"skipped {result['skipped']} candidates without a registry company or catalog")
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
