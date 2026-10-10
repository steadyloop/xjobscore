"""Fill `tenants.name` from the board page's own ``<title>``.

Greenhouse and workable answer the probe with a name already, so 99.9% of
their tenants are named without a second request. Ashby, lever and oracle do
not -- their posting APIs return jobs and nothing else -- yet all three title
their public board after the employer. That title is the vendor's own record
of who the tenant is, which beats guessing from the token slug.

``<title>`` rather than the og: tags, measured per vendor: lever's og:title
appends " jobs" and oracle's appends " Careers", while the plain title is the
name on its own. Ashby appends " Jobs" to both, which :data:`SUFFIX` removes.

One GET per board, gated per host like `peek_sweep`, and resumable for free --
a row that has a name is not selected again.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections import defaultdict
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

import httpx

__all__ = ["name_from_title", "name_boards", "SUPPORTED"]

# The vendors whose board is HTML and whose API withholds the name. Greenhouse
# and workable are absent because they answer with one; workday is absent
# because its board renders client-side and ships an empty <title>.
SUPPORTED = ("ashby", "lever", "oracle")

HEADERS = {"User-Agent": "xjobscore-namer/0.1"}

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# A title assembled inside a JavaScript string keeps that string's escapes:
# Macy's arrived as "Macy\\'s" and Carrière as "Carri\\u00E8re".
JS_QUOTE = re.compile(r"\\([\"'\\])")
JS_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")

# Oracle only. Its board title is written by the customer, not the vendor, so
# it arrives as free text -- often the *site's* name with the employer buried
# in it, and in any language. These strip the parts that are demonstrably
# Oracle's own furniture.
ORACLE_SUFFIX = re.compile(
    r"(?:[\s ]*[-|\u2013\u2014:][\s ]*|[\s ]+)"
    # The furniture wraps the boilerplate on both sides: "External Career
    # Site", "Career Site Minimal", "Minimal Career Site". Only words that are
    # unambiguously Oracle's are listed -- "global" is not, because "TWE
    # Global Careers" has to keep its Global.
    r"(?:(?:external|internal|minimal|classic|responsive)\s+)?"
    r"(all open jobs|current openings|open positions|job openings"
    r"|job board|job opportunities|careers? (?:page|site|portal|home)s?"
    r"|careers?|jobs?|vacancies|hiring)"
    r"(?:\s+(?:minimal|classic|responsive))?\s*$",
    re.I,
)
ORACLE_PREFIX = re.compile(
    r"^(?:careers?|jobs?|vacancies|work|opportunities)\s+(?:at|with|@)\s+",
    re.I,
)

# Titles that name the product rather than the customer. Oracle ships these as
# defaults and a tenant that never edited one has no name to give. The
# non-English entries are the same default in another locale, which is why a
# blocklist can only ever be a floor -- see `confidence` below.
GENERIC = frozenset({
    "", "home", "welcome", "talent acquisition", "recruitment", "recruiting",
    "job search", "search jobs", "opportunities", "employment", "apply",
    "candidate experience", "work with us", "join us", "team",
    "job", "jobs", "career", "careers", "job board", "career site",
    "careers site", "career sites", "openings", "open positions",
    "candidate experience site", "candidate experience portal",
    "external career site", "internal career site", "career site minimal",
    # What is left when the whole title was furniture: "Global Career Site"
    # strips to "Global", which names no employer.
    "global", "external", "internal", "new", "main", "corporate", "group",
    # Oracle's default, localised.
    "sitio de experiencia de candidatos", "site d'experience des candidats",
    "site d\u2019experience des candidats", "carriere", "carrière",
    "kandidatenerlebnis-website", "sito esperienza candidati",
})

# A challenge page or a dead board answers 200 with a title that is not a
# name. Matched anywhere, not anchored: "Not found - 404 error" leads with the
# words but "Attention Required! | Cloudflare" does not.
NOT_A_NAME = re.compile(
    r"(404|not found|just a moment|attention required|access denied|forbidden"
    r"|unavailable|page (?:not found|error)|server error|bad gateway"
    r"|are you a robot|verify you are human)",
    re.I,
)

# A name, not a tagline.
MAX_NAME = 80

# Ashby titles every board "<Employer> Jobs" -- 119 of 120 live boards
# sampled, the odd one being a board whose owner set no name at all. The
# whitespace is required, so a company called "TowardJobs" cannot be truncated
# to "Toward": no separator, no match, and the board is refused rather than
# renamed.
ASHBY_TITLE = re.compile(r"^(?P<name>.+?)\s+Jobs$", re.I)


def _clean(raw: str) -> str:
    """Undo the escaping layers a title picks up on its way into the HTML."""
    text = JS_UNICODE.sub(lambda m: chr(int(m.group(1), 16)), raw)
    text = unescape(JS_QUOTE.sub(r"\1", text))
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _plausible(name: str) -> bool:
    return 2 <= len(name) <= MAX_NAME and name.casefold() not in GENERIC


def name_from_title(title: str, ats: str) -> tuple[str | None, str]:
    """(name, confidence) for one board title.

    Three vendors, three contracts, so three rules -- a single rule for all of
    them is what produced every bug this module has had. Ashby and lever
    publish a format; oracle publishes whatever its customer typed.

    Confidence is the honest part. ``exact`` means the vendor's own format
    matched and nothing was guessed. ``stripped`` means recognisable
    furniture was removed to get here, which is right far more often than not
    but cannot be proven for a title nobody has seen. Callers that must not be
    wrong should treat ``stripped`` as a review queue, not as fact.
    """
    text = _clean(title)
    if not text or NOT_A_NAME.search(text):
        return None, "none"

    if ats == "ashby":
        matched = ASHBY_TITLE.match(text)
        if not matched:
            return None, "none"
        name = matched.group("name").strip()
        return (name, "exact") if _plausible(name) else (None, "none")

    if ats == "lever":
        # Verbatim. Lever prints the account name and nothing else, so any
        # stripping here can only damage a real name: "TowardJobs" and
        # "Gojob" are employers, not boilerplate.
        return (text, "exact") if _plausible(text) else (None, "none")

    stripped = ORACLE_SUFFIX.sub("", ORACLE_PREFIX.sub("", text)).strip(
        " -|:\u2013\u2014"
    )
    if not _plausible(stripped):
        return None, "none"
    return stripped, "exact" if stripped == text else "stripped"


def unnamed(
    db_path: Path, sources=SUPPORTED, *, limit_per_source: int | None = None
) -> list[tuple[str, str, str]]:
    """Live tenants of *sources* that carry no name, as (ats, key, url)."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows: list[tuple[str, str, str]] = []
        for ats in sources:
            # ORDER BY key, not random: a rerun of a partial pass then walks
            # the same order and the trial is reproducible.
            found = con.execute(
                "SELECT ats, key, canonical_url FROM tenants"
                "  WHERE status = 'live' AND ats = ?"
                "    AND (name IS NULL OR name = '')"
                "  ORDER BY key" + (" LIMIT ?" if limit_per_source else ""),
                (ats, limit_per_source) if limit_per_source else (ats,),
            ).fetchall()
            rows.extend((r["ats"], r["key"], r["canonical_url"]) for r in found)
        return rows
    finally:
        con.close()


async def name_boards(
    db_path: Path,
    sources=SUPPORTED,
    *,
    limit_per_source: int | None = None,
    per_host: int = 4,
    timeout: float = 20.0,
    dry_run: bool = False,
    on_row=None,
) -> dict:
    """Fetch each unnamed board and store the name its title carries.

    Returns counts by source. ``dry_run`` fetches and reports without writing,
    which is how the extraction rule gets judged before 5,000 rows depend on
    it.
    """
    rows = unnamed(db_path, sources, limit_per_source=limit_per_source)
    gates: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(per_host)
    )
    results: list[tuple[str, str, str | None, str]] = []
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=HEADERS,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    ) as client:

        async def one(ats: str, key: str, url: str) -> None:
            # Gate on the host, not the vendor: oracle gives every tenant its
            # own host, so its boards are not in contention with each other
            # the way ashby's and lever's single host is.
            host = (urlsplit(url).hostname or ats).lower()
            async with gates[host]:
                try:
                    response = await client.get(url)
                    found = TITLE.search(response.text)
                    name, note = (
                        name_from_title(found.group(1), ats)
                        if response.status_code == 200 and found
                        else (None, "none")
                    )
                    if not name:
                        note = (
                            "no_name" if response.status_code == 200
                            else f"http_{response.status_code}"
                        )
                except httpx.HTTPError as exc:
                    name, note = None, type(exc).__name__
            async with lock:
                results.append((ats, key, name, note))
                if on_row:
                    on_row(len(results), len(rows), ats, key, name, note)

        await asyncio.gather(*(one(*row) for row in rows))

    named = [r for r in results if r[2]]
    if named and not dry_run:
        con = sqlite3.connect(db_path)
        try:
            # Guarded on emptiness so a name learned by another path since the
            # SELECT is never overwritten by a title.
            con.executemany(
                "UPDATE tenants SET name = ?"
                "  WHERE ats = ? AND key = ? AND (name IS NULL OR name = '')",
                [(name, ats, key) for ats, key, name, _ in named],
            )
            con.commit()
        finally:
            con.close()

    by_source: dict[str, dict[str, int]] = {}
    for ats, _, name, note in results:
        counts = by_source.setdefault(
            ats, {"fetched": 0, "named": 0, "exact": 0, "stripped": 0}
        )
        counts["fetched"] += 1
        counts["named"] += bool(name)
        if name:
            counts[note] += 1
    return {
        # `stripped` is the cohort a reviewer should look at: recognisable
        # furniture was removed to reach the name, which is usually but not
        # provably right.
        "stripped": sum(1 for _, _, name, note in results if name and note == "stripped"),
        "written": 0 if dry_run else len(named),
        "fetched": len(results),
        "named": len(named),
        "by_source": by_source,
        "rows": results,
    }
