"""Command line interface for the standalone keyscan subsystem."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from .core import HARVEST_RPS, TARGET_MBPS, ActiveRunError, Keyscan, KeyscanError, selftest
from .rules import SAMPLES
from .enumerate import enumerate_tokens
from .name_boards import SUPPORTED, name_boards
from .search import DEFAULT_TITLES, board_matches, index_titles, search_titles
from .verify import verify_sources


def _methods(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _elapsed(milliseconds: object) -> str:
    try:
        seconds = max(0, round(int(milliseconds) / 1000))
    except (TypeError, ValueError):
        return "?"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _progress_line(entry: dict) -> str | None:
    """Render only milestones worth occupying a terminal line."""
    # Details moved under `detail`; runs logged before that wrote them at the
    # top level. Reading a flattened view of both keeps old run.jsonl files
    # renderable, which matters because `keyscan logs` reads them by run id.
    entry = {**entry, **(entry.get("detail") or {})}
    stamp = str(entry.get("at", ""))[11:19] or "--:--:--"
    prefix = f"[keyscan {stamp}]"
    message = entry.get("message")
    event = entry.get("event")

    if message == "run started":
        methods = ",".join(entry.get("methods", ()))
        if entry.get("crawl_ids"):
            scope = ",".join(entry["crawl_ids"])
        elif entry.get("date_from"):
            scope = f"{entry['date_from']} through {entry.get('date_to')}"
        elif entry.get("crawls") is not None:
            scope = f"{entry['crawls']} snapshots"
        else:
            scope = "no crawl scan"
        return f"{prefix} run started | {methods} | {scope}"
    if message == "run resumed":
        return f"{prefix} run resumed"
    # `started` is now the message and the function says which one, so the
    # line reads "crawl started" rather than the old "phase commoncrawl".
    if message == "started" and entry.get("function") in {"crawl", "scan", "validate", "wayback"}:
        return f"{prefix} {entry['function']} started"
    if event == "plan":
        return (
            f"{prefix} {entry.get('crawl')} plan | {entry.get('row_groups', '?')} row groups in "
            f"{entry.get('files', '?')}/{entry.get('of', '?')} files | "
            f"{round((entry.get('bytes') or 0) / 1e6)}MB to read"
        )
    if event == "unsupported_index":
        return f"{prefix} {entry.get('crawl')} skipped | {entry.get('reason')}"
    if event == "network_summary":
        failed = entry.get("failed", 0)
        return (
            f"{prefix} {entry.get('requests', 0)} requests | {failed} failed | "
            f"median {entry.get('median_ms', '?')}ms"
        )
    if event == "wayback_plan":
        return f"{prefix} wayback | {entry.get('pairs', 0)} vendor/year pairs"
    if event == "wayback_pair":
        return (
            f"{prefix} wayback {entry.get('year')} {entry.get('source')} | "
            f"{entry.get('candidates', 0)} candidates | pair {entry.get('pair')}/{entry.get('pairs')} | "
            f"{entry.get('requests', 0)} requests | {_elapsed(entry.get('elapsed_ms'))}"
        )
    if message == "wayback backoff":
        return f"{prefix} wayback | {entry.get('cause')} | waiting {entry.get('pause_s', 0):g}s"
    if message == "wayback pair failed":
        return f"{prefix} wayback {entry.get('year')} {entry.get('source')} failed | {entry.get('error')} | continuing"
    if message == "scanning secondary job boards":
        return f"{prefix} board scan | {entry.get('start', 0)}/{entry.get('total', '?')} already complete"
    if event == "crawl_start":
        return (
            f"{prefix} {entry.get('crawl')} start | snapshot "
            f"{entry.get('snapshot', '?')}/{entry.get('snapshots', '?')} | "
            f"{entry.get('files', '?')} files | {entry.get('vendors', '?')} vendors"
        )
    if event == "scan_checkpoint":
        files = entry.get("files", "?")
        total = entry.get("total_files", "?")
        percent = ""
        if isinstance(files, int) and isinstance(total, int) and total:
            percent = f" ({round(100 * files / total)}%)"
        return (
            f"{prefix} {entry.get('crawl')} | files {files}/{total}{percent} | "
            f"+{entry.get('candidates', 0)} candidates | {entry.get('vendors', 0)} vendors | "
            f"{_elapsed(entry.get('took_ms'))}"
        )
    if event == "batch_retry":
        error = str(entry.get("error", "remote read failed")).replace("\n", " ")
        if len(error) > 100:
            error = error[:97] + "..."
        backoff = entry.get("backoff_s")
        waiting = f" | waiting {backoff:g}s" if isinstance(backoff, (int, float)) else ""
        return (
            f"{prefix} {entry.get('crawl')} | retry {entry.get('attempt')}/{entry.get('max_attempts')} "
            f"after {entry.get('files', 0)} files{waiting} | {error}"
        )
    if event == "operation_timing" and entry.get("operation") == "commoncrawl_merged_scan":
        if entry.get("outcome") == "ok":
            return (
                f"{prefix} {entry.get('crawl')} complete | {entry.get('files', 0)} files | "
                f"{entry.get('candidates', 0)} candidates | {_elapsed(entry.get('elapsed_ms'))}"
            )
        return f"{prefix} {entry.get('crawl')} failed | {_elapsed(entry.get('elapsed_ms'))} | continuing"
    if message == "validating candidates":
        return f"{prefix} validation sweep | {entry.get('total', 0)} candidates | {entry.get('sources', 0)} vendors"
    if message == "validation progress":
        return f"{prefix} validation | {entry.get('validated', 0)} done | {entry.get('queued', 0)} queued"
    if message == "validation stopped; vendor refused us":
        return f"{prefix} validation paused by vendor refusal | {entry.get('remaining', 0)} remain"
    if message in {"run completed", "run interrupted", "run cancelled", "run failed"}:
        counts = " | ".join(
            f"{key} {entry[key]}" for key in ("found", "live", "dead", "unknown") if key in entry
        )
        suffix = f" | {counts}" if counts else ""
        return f"{prefix} {message}{suffix}"
    return None


def _print_progress(entry: dict) -> None:
    line = _progress_line(entry)
    if line:
        print(line, file=sys.stderr, flush=True)


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
    crawl = sub.add_parser(
        "crawl", help="scan Common Crawl for one or more vendors"
    )
    crawl.add_argument(
        "crawl_ids", metavar="ID[,ID,...]", nargs="?",
        help="Common Crawl snapshot ids to scan (see `keyscan list`); omit to use --from/--to instead",
    )
    crawl.add_argument(
        "--from", dest="date_from", metavar="YYYY-MM-DD",
        help="inclusive beginning of the Common Crawl date range (selects every overlapping snapshot); "
        "alternative to crawl ids",
    )
    crawl.add_argument(
        "--to", dest="date_to", metavar="YYYY-MM-DD",
        help="inclusive end of the Common Crawl date range",
    )
    crawl.add_argument(
        "--vendor", metavar="ATS[,ATS,...]",
        help="comma-separated ATS sources or group names (e.g. tier1); default: all",
    )
    crawl.add_argument(
        "--target-mbps", type=float, default=TARGET_MBPS,
        help=f"average Common Crawl transfer ceiling (default: {TARGET_MBPS:g}); 0 disables pacing",
    )
    crawl.add_argument(
        "--validate", action="store_true",
        help="probe each discovered token against its vendor (slow; hits vendor hosts)",
    )
    crawl.add_argument(
        "--rescan", action="store_true",
        help="scan vendor/snapshot pairs again even if an earlier run finished them",
    )
    crawl.add_argument("--quiet", action="store_true", help="suppress live progress on stderr")
    scan_cmd = sub.add_parser(
        "scan", help="scan public GitHub-hosted job board listings for one or more vendors"
    )
    scan_cmd.add_argument(
        "--vendor", metavar="ATS[,ATS,...]",
        help="comma-separated ATS sources or group names (e.g. tier1); default: all",
    )
    scan_cmd.add_argument(
        "--archives", action="store_true",
        help="also read the boards' frozen archived/*.json history",
    )
    scan_cmd.add_argument(
        "--validate", action="store_true",
        help="probe each discovered token against its vendor (slow; hits vendor hosts)",
    )
    scan_cmd.add_argument("--quiet", action="store_true", help="suppress live progress on stderr")
    wayback_cmd = sub.add_parser(
        "wayback", help="search the Internet Archive's Wayback Machine for one or more vendors"
    )
    wayback_cmd.add_argument(
        "--vendor", metavar="ATS[,ATS,...]", default="tier1,tier2",
        help="comma-separated ATS sources or group names (default: tier1,tier2)",
    )
    wayback_cmd.add_argument(
        "--years", metavar="YYYY[,YYYY,...]",
        help="capture years to search (default: every year from 2013 to now)",
    )
    wayback_cmd.add_argument("--from-year", type=int, metavar="YYYY", help="first capture year (inclusive)")
    wayback_cmd.add_argument("--to-year", type=int, metavar="YYYY", help="last capture year (inclusive)")
    wayback_cmd.add_argument(
        "--validate", action="store_true",
        help="probe each discovered token against its vendor (slow; hits vendor hosts)",
    )
    wayback_cmd.add_argument(
        "--rescan", action="store_true",
        help="search vendor/year pairs again even if an earlier run finished them",
    )
    wayback_cmd.add_argument("--quiet", action="store_true", help="suppress live progress on stderr")
    validate_cmd = sub.add_parser(
        "validate", help="re-probe discovered tokens for one or more vendors"
    )
    validate_cmd.add_argument(
        "--vendor", metavar="ATS[,ATS,...]",
        help="comma-separated ATS sources or group names (e.g. tier1); default: all",
    )
    validate_cmd.add_argument("--quiet", action="store_true", help="suppress live progress on stderr")
    verify = sub.add_parser(
        "verify", help="sample-verify vendor rules against live data without writing to the tenants table"
    )
    verify.add_argument("--sources", required=True, help="comma-separated ATS sources or group names (e.g. tier1)")
    verify.add_argument("--crawls", type=int, default=1, help="Common Crawl snapshots to scan (default: 1)")
    verify.add_argument("--max-probes", type=int, default=40, help="probe cap per vendor before giving up (default: 40)")
    verify.add_argument("--json", action="store_true", help="print raw JSON instead of a summary table")
    list_cmd = sub.add_parser(
        "list", help="list supported Common Crawl Parquet snapshots, newest first"
    )
    list_cmd.add_argument("--json", action="store_true", help="print raw JSON instead of a table")
    sub.add_parser("push-tokens", help="add every keyscan token to xjobscore's token table (unvalidated, insert-only)")
    robots = sub.add_parser("robots", help="report shared ATS hosts that block CCBot")
    robots.add_argument("--sources", help="comma-separated ATS sources; default: all")
    resume = sub.add_parser("resume", help="resume an interrupted run")
    resume.add_argument("run_id")
    resume.add_argument("--quiet", action="store_true", help="suppress live progress on stderr")
    status = sub.add_parser("status", help="show the latest or named run")
    status.add_argument("run_id", nargs="?")
    cancel = sub.add_parser("cancel", help="request cancellation of a run")
    cancel.add_argument("run_id")
    candidates = sub.add_parser("candidates", help="list discovered candidates")
    candidates.add_argument("--status", choices=("unknown", "live", "dead"))
    candidates.add_argument("--source")
    candidates.add_argument("--query", default="")
    candidates.add_argument("--limit", type=int, default=100)
    index_cmd = sub.add_parser(
        "index-titles", help="load work/peek/titles.jsonl into the searchable table"
    )
    index_cmd.add_argument(
        "--titles", type=Path, default=DEFAULT_TITLES, help="path to titles.jsonl"
    )
    index_cmd.add_argument(
        "--rebuild", action="store_true", help="discard the table and reload it"
    )
    search_cmd = sub.add_parser("search", help="search peeked job titles by regex")
    search_cmd.add_argument("pattern", nargs="?", help="regex; omit with --regex-file")
    search_cmd.add_argument(
        "--regex-file", type=Path, help="read the pattern from a file instead"
    )
    search_cmd.add_argument("--veto", help="regex dropping titles the pattern caught")
    search_cmd.add_argument("--source", help="restrict to one ats label")
    search_cmd.add_argument(
        "--by-board", action="store_true", help="count hits per board, not per title"
    )
    search_cmd.add_argument(
        "--min-hits", type=int, default=1, help="with --by-board: hits to keep a board"
    )
    search_cmd.add_argument("--limit", type=int, default=100)
    name_cmd = sub.add_parser(
        "name-boards", help="fill tenants.name from each board page's <title>"
    )
    name_cmd.add_argument(
        "--vendor", help=f"comma-separated; default {','.join(SUPPORTED)}"
    )
    name_cmd.add_argument(
        "--limit", type=int, help="per vendor; omit to name every unnamed board"
    )
    name_cmd.add_argument(
        "--per-host", type=int, default=4, help="concurrent requests per host"
    )
    name_cmd.add_argument(
        "--dry-run", action="store_true", help="fetch and report without writing"
    )
    enum_cmd = sub.add_parser(
        "enum", help="guess one vendor's tokens from a wordlist and probe each guess"
    )
    enum_cmd.add_argument("--vendor", required=True, help="one ATS source, e.g. icims")
    enum_cmd.add_argument(
        "--words", default="-", help="wordlist file, one word or domain per line (default: stdin)"
    )
    enum_cmd.add_argument("--concurrency", type=int, default=16, help="probes in flight (default: 16)")
    enum_cmd.add_argument("--dry-run", action="store_true", help="probe and report without writing")
    footers_cmd = sub.add_parser(
        "footers", help="harvest Common Crawl parquet footers so later scans read only matching files"
    )
    footers_cmd.add_argument(
        "crawl_ids", metavar="ID[,ID,...]", nargs="?",
        help="snapshot ids to harvest (see `keyscan list`); omit for every supported snapshot; explicit pre-2013 ids are skipped",
    )
    footers_cmd.add_argument(
        "--rps", type=float, default=HARVEST_RPS,
        help=f"request-rate ceiling against Common Crawl (default: {HARVEST_RPS:g})",
    )
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

    progress = (
        _print_progress
        if args.command in {"crawl", "scan", "validate", "resume", "wayback"} and not getattr(args, "quiet", False)
        else None
    )
    # Discoveries go straight into xjobscore's `token` table when its DSN is
    # known -- the same `.env` the rest of the repo reads.
    from xjobscore.run import _load_dotenv

    _load_dotenv()
    scan = Keyscan(
        args.workdir,
        target_mbps=getattr(args, "target_mbps", TARGET_MBPS),
        progress=progress,
        token_dsn=os.environ.get("DATABASE_URL"),
    )
    try:
        if args.command == "crawl":
            run_id = asyncio.run(
                scan.run(
                    ["commoncrawl", *(["validate"] if args.validate else [])], sources=_methods(args.vendor) or None,
                    crawl_ids=_methods(args.crawl_ids) or None,
                    date_from=args.date_from, date_to=args.date_to,
                    rescan=args.rescan,
                )
            )
            _print(scan.status(run_id))
        elif args.command == "scan":
            run_id = asyncio.run(
                scan.run(["board", *(["validate"] if args.validate else [])], sources=_methods(args.vendor) or None, archives=args.archives)
            )
            _print(scan.status(run_id))
        elif args.command == "wayback":
            years = [int(year) for year in _methods(args.years)] if args.years else None
            if years and (args.from_year or args.to_year):
                parser.error("use --years or --from-year/--to-year, not both")
            if args.from_year or args.to_year:
                first = args.from_year or 2013
                last = args.to_year or datetime.now(timezone.utc).year
                if first > last:
                    parser.error("--from-year is after --to-year")
                years = list(range(first, last + 1))
            run_id = asyncio.run(
                scan.run(
                    ["wayback", *(["validate"] if args.validate else [])],
                    sources=_methods(args.vendor) or None, years=years, rescan=args.rescan,
                )
            )
            _print(scan.status(run_id))
        elif args.command == "validate":
            run_id = asyncio.run(scan.run(["validate"], sources=_methods(args.vendor) or None))
            _print(scan.status(run_id))
        elif args.command == "list":
            result = asyncio.run(scan.list_crawls())
            if args.json:
                _print(result)
            else:
                if result["source"] == "cache":
                    print(
                        f"# Common Crawl unreachable ({result['error']}); "
                        f"showing the list from {result['fetched_at']}",
                        file=sys.stderr,
                    )
                # Column widths sized to the actual data, `column -t` style --
                # a fixed width would either clip a long crawl name or waste
                # space on every short one.
                rows = result["crawls"]
                id_w = max((len(row["id"]) for row in rows), default=0)
                name_w = max((len(row["name"]) for row in rows), default=0)
                from_w = max((len(row["from"]) for row in rows), default=0)
                for row in rows:
                    print(f"{row['id']:<{id_w}}  {row['name']:<{name_w}}  {row['from']:<{from_w}}  {row['to']}")
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
        elif args.command == "push-tokens":
            _print(asyncio.run(scan.push_tokens()))
        elif args.command == "footers":
            crawl_ids = _methods(args.crawl_ids) or [
                row["id"] for row in asyncio.run(scan.list_crawls())["crawls"]
            ]

            def on_event(event: dict) -> None:
                print(f"[keyscan footers] {json.dumps(event, default=str)}", file=sys.stderr, flush=True)

            _print(scan.harvest_footers(crawl_ids, target_rps=args.rps, on_event=on_event))
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
        elif args.command == "index-titles":
            _print(index_titles(scan.db_path, args.titles, rebuild=args.rebuild))
        elif args.command == "search":
            pattern = (
                args.regex_file.read_text().strip()
                if args.regex_file
                else args.pattern
            )
            if not pattern:
                parser.error("search needs a pattern or --regex-file")
            if args.by_board:
                _print(board_matches(
                    scan.db_path, pattern, ats=args.source,
                    veto=args.veto, min_hits=args.min_hits,
                ))
            else:
                _print(search_titles(
                    scan.db_path, pattern, ats=args.source, limit=args.limit
                ))
        elif args.command == "name-boards":
            sources = tuple(_methods(args.vendor)) or SUPPORTED
            unknown = [v for v in sources if v not in SUPPORTED]
            if unknown:
                parser.error(
                    f"name-boards has no title rule for {', '.join(unknown)}; "
                    f"supported: {', '.join(SUPPORTED)}"
                )
            result = asyncio.run(name_boards(
                scan.db_path, sources,
                limit_per_source=args.limit,
                per_host=args.per_host,
                dry_run=args.dry_run,
            ))
            rows = result.pop("rows")
            if args.dry_run:
                for ats, key, name, note in sorted(rows):
                    print(f"{ats:<9} {key[:40]:<40} {note:<9} {name or '-'}")
            _print(result)
        elif args.command == "enum":
            scan.initialize()
            source = sys.stdin if args.words == "-" else Path(args.words).open()
            with source:
                words = source.read().splitlines()

            def on_row(done: int, total: int, token: str, status: str, count: int | None) -> None:
                if status == "live":
                    print(f"[keyscan enum] live {args.vendor}/{token} jobs={count}", file=sys.stderr)
                if done % 250 == 0 or done == total:
                    print(f"[keyscan enum] {done}/{total} probed", file=sys.stderr)

            _print(asyncio.run(enumerate_tokens(
                str(scan.db_path), args.vendor, words,
                concurrency=args.concurrency, dry_run=args.dry_run, on_row=on_row,
            )))
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
