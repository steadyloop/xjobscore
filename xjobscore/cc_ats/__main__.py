"""CLI: discover -> probe (no DB dependency).

Use feed_ats_bind for load and bind (Postgres writes). Kept separate because
they fail differently: the scan is slow and network-bound, the probe is
rate-limited and takes hours, and only the bind stage touches Postgres.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from cc_ats import discover, probe, robots, vendors

CC_ATS_DIR = Path(__file__).parent
DEFAULT_WORKDIR = CC_ATS_DIR / "work"


def parse_vendors(value: str | None) -> set[str] | None:
    return {v.strip() for v in value.split(",") if v.strip()} if value else None


def cmd_selftest() -> int:
    """Check the compiled patterns against the fixtures in vendors.py.

    Cheap guard on the part of the registry most likely to rot: pattern order.
    Greenhouse and Workday both depend on a specific one, and getting it wrong
    yields tenants called "embed" or "en-US" that probe as live.
    """
    failures = 0
    for url, expected in vendors.SAMPLE_URLS:
        actual = vendors.extract(url)
        if actual != expected:
            failures += 1
            print(f"FAIL {url}\n  expected {expected}\n  got      {actual}")
    total = len(vendors.SAMPLE_URLS)
    print(f"{total - failures}/{total} extraction fixtures pass")
    print(f"{len(vendors.VENDORS)} vendors registered")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cc_ats", description=__doc__)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--vendors", help="comma-separated ats names; default all")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("discover", help="scan Common Crawl for tokens")
    scan.add_argument("--crawls", type=int, default=2)
    scan.add_argument("--memory-limit", default="6GB")
    scan.add_argument("--threads", type=int, default=4)

    check = sub.add_parser("probe", help="confirm which tokens are real boards")
    check.add_argument("--limit", type=int, help="probe only the first N; use for a smoke test")
    check.add_argument("--concurrency", type=int, default=3)
    check.add_argument("--per-vendor", type=int, default=6)

    sub.add_parser("selftest", help="check extraction patterns against fixtures")
    sub.add_parser("robots", help="which vendors block CCBot (explains low token counts)")

    args = parser.parse_args()
    only = parse_vendors(args.vendors)
    args.workdir.mkdir(parents=True, exist_ok=True)

    if args.command == "selftest":
        return cmd_selftest()

    if args.command == "robots":
        asyncio.run(robots.run())
        return 0

    if args.command == "discover":
        discover.run(
            args.workdir,
            crawls=args.crawls,
            only=only,
            memory_limit=args.memory_limit,
            threads=args.threads,
        )
        return 0

    if args.command == "probe":
        candidates = discover.load_candidates(args.workdir, only)
        asyncio.run(
            probe.run(
                args.workdir,
                candidates,
                per_vendor=args.per_vendor,
                concurrency=args.concurrency,
                limit=args.limit,
            )
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
