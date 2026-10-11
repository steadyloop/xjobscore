"""Harvest one square logo per company from its own site.

Companies are identified in the UI by a 40px box; this fills it. The source of
truth is the site itself -- `apple-touch-icon` first because it is the only
icon vendors are required to ship at a usable size (180px), then any declared
`rel=icon`, then the `/favicon.ico` every server still answers. An apex domain
that does not answer at all is retried as `www.`; Prudential's apex times out
while its `www` serves.

When the site yields nothing drawable, Google's favicon service is asked next.
It serves what Google's crawler collected rather than fetching live, so it gets
past bot walls that let Googlebot through (Blue Origin's Vercel checkpoint
answers every request of ours with 429) and it knows manifest icons a homepage
never links. It is undocumented, so it is the fallback, never the source of
truth; a 404 is its "no icon" answer and carries a placeholder globe that the
status check already discards.

A mark under `MIN_EDGE` is kept only as a last resort: a soft 16px icon in the
box still beats an empty one, but any larger mark from any source wins first.

Anything wider than `MAX_EDGE` is downscaled on the way in. The box draws the
mark at 30 CSS px, so 128 covers a 3x display and every pixel above that is
weight no one can see -- sites ship 512px app icons and, in one case, a 6900px
hero image. Smaller icons are left exactly as served: upscaling a 32px favicon
invents no detail and only costs bytes.

A row with NULL `bytes` records a domain that refused to answer, so a rerun
skips it rather than paying the timeout again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx
from PIL import Image

from xjobscore import store

if TYPE_CHECKING:
    from asyncpg import Pool

log = logging.getLogger(__name__)

# Sites serve the browser UA a different homepage than they serve curl -- some
# serve none at all -- and the icon links only exist on the former.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HOMEPAGE_TIMEOUT = 12.0
ICON_TIMEOUT = 8.0
# Enough for every <head> ever written; homepages that inline their app run to
# megabytes and the icon links are not down there.
HOMEPAGE_BYTES = 300_000
# A 512px PNG is ~100KB. Past that it is a hero image mislabelled as an icon.
MAX_LOGO_BYTES = 250_000
# The card box is 40px with 5px of padding, so the mark draws at 30 CSS px --
# 90 device px on a 3x display. 128 clears that with room and is a size icon
# encoders are already tuned for.
MAX_EDGE = 128
# Below this a mark is drawn larger than it was authored and reads soft. 16px
# favicons are the offenders; by 32px even a detailed mark survives the 30px
# box. Measured by eye against the real card, not assumed. A smaller mark is
# held back and stored only when no source has anything at this size.
MIN_EDGE = 32
CONCURRENCY = 24
GOOGLE_FAVICON = "https://www.google.com/s2/favicons?domain={domain}&sz=" + str(MAX_EDGE)

LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
REL_ATTR = re.compile(r"\brel\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
HREF_ATTR = re.compile(r"\bhref\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
SIZES_ATTR = re.compile(r"\bsizes\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)

# Magic bytes, so a 200 that is really an HTML error page is not stored as an
# image. SVG is text and has no magic number; it is matched separately.
RASTER_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\x00\x00\x01\x00", b"GIF87a", b"GIF89a", b"\xff\xd8\xff")


def _is_image(payload: bytes) -> bool:
    if payload.startswith(RASTER_MAGIC):
        return True
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return True
    head = payload[:512].lstrip().lower()
    return head.startswith(b"<?xml") or head.startswith(b"<svg")


def _edge(payload: bytes) -> int | None:
    """Longest side in pixels, or None for SVG and anything Pillow cannot read."""
    if not payload.startswith(RASTER_MAGIC) and not payload.startswith(b"RIFF"):
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            return max(image.size)
    except Exception:  # noqa: BLE001 -- Pillow raises broadly on junk
        return None


def too_small(payload: bytes) -> bool:
    """True when a mark is too low-resolution to draw sharply in the card box.

    SVG and anything Pillow cannot read are never rejected: the first is
    resolution-independent and the second is judged by the browser, not here.
    """
    edge = _edge(payload)
    return edge is not None and edge < MIN_EDGE


def normalize(payload: bytes) -> bytes:
    """Downscale an oversized raster to `MAX_EDGE`, or return it untouched.

    Returns the input unchanged for SVG (resolution-independent already), for
    anything already small enough, and for anything Pillow cannot read -- a
    logo that decodes in a browser but not here is still worth keeping, so a
    decode failure must never cost us the image.
    """
    if not payload.startswith(RASTER_MAGIC) and not payload.startswith(b"RIFF"):
        return payload
    try:
        with Image.open(BytesIO(payload)) as image:
            # ICO files hold several frames; Pillow opens the largest, which is
            # the one worth keeping.
            if max(image.size) <= MAX_EDGE:
                return payload
            # RGBA throughout: these marks are overwhelmingly transparent, and
            # flattening one onto a guessed background is the one edit here
            # that would actually be visible.
            resized = image.convert("RGBA")
            resized.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="PNG", optimize=True)
    except Exception as error:  # noqa: BLE001 -- Pillow raises broadly on junk
        log.debug("normalize failed, keeping original: %s", error)
        return payload
    shrunk = buffer.getvalue()
    # An optimized PNG of a photographic icon can exceed the JPEG it came from.
    return shrunk if len(shrunk) < len(payload) else payload


def _icon_candidates(html: str, base: str) -> list[str]:
    """Return icon hrefs, best first.

    `apple-touch-icon` wins on declaration alone -- it is defined as a large
    square -- and within each kind the biggest declared `sizes` wins, because a
    16px favicon upscaled into the 40px box looks worse than nothing.
    """
    apple: list[tuple[int, str]] = []
    generic: list[tuple[int, str]] = []
    for tag in LINK_TAG.findall(html):
        rel_match = REL_ATTR.search(tag)
        href_match = HREF_ATTR.search(tag)
        if not rel_match or not href_match:
            continue
        rel = rel_match.group(1).lower()
        href = href_match.group(1).strip()
        if not href or href.startswith("data:"):
            continue
        sizes_match = SIZES_ATTR.search(tag)
        edge = 0
        if sizes_match:
            found = re.findall(r"(\d+)x\d+", sizes_match.group(1), re.IGNORECASE)
            edge = max((int(n) for n in found), default=0)
        if "apple-touch-icon" in rel:
            apple.append((edge, urljoin(base, href)))
        elif "icon" in rel:
            generic.append((edge, urljoin(base, href)))

    ordered = [url for _, url in sorted(apple, key=lambda p: -p[0])]
    ordered += [url for _, url in sorted(generic, key=lambda p: -p[0])]
    ordered.append(urljoin(base, "/favicon.ico"))
    seen: set[str] = set()
    return [url for url in ordered if not (url in seen or seen.add(url))]


class _Small:
    """The largest under-`MIN_EDGE` mark seen so far, held as a last resort."""

    def __init__(self) -> None:
        self.payload: bytes | None = None
        self.edge = 0

    def offer(self, payload: bytes) -> None:
        edge = _edge(payload) or 0
        if self.payload is None or edge > self.edge:
            self.payload, self.edge = payload, edge


async def _get_image(
    client: httpx.AsyncClient, domain: str, url: str, small: _Small
) -> bytes | None:
    """Fetch `url`; return a drawable image, or hold a too-small one in `small`."""
    try:
        response = await client.get(url, timeout=ICON_TIMEOUT)
    except httpx.HTTPError as error:
        log.debug("%s: %s: %s", domain, url, error)
        return None
    payload = response.content
    if response.status_code >= 400 or not payload or len(payload) > MAX_LOGO_BYTES:
        return None
    if not _is_image(payload):
        return None
    if too_small(payload):
        log.debug("%s: %s too small to draw, held as last resort", domain, url)
        small.offer(payload)
        return None
    log.debug("%s: took %s (%d bytes)", domain, url, len(payload))
    return normalize(payload)


async def _from_site(
    client: httpx.AsyncClient, host: str, small: _Small
) -> tuple[bytes | None, bool]:
    """Try one host's declared icons. Returns (logo, whether the homepage answered)."""
    home = f"https://{host}/"
    html = ""
    answered = False
    try:
        response = await client.get(home, timeout=HOMEPAGE_TIMEOUT)
        answered = True
        if response.status_code < 400:
            html = response.text[:HOMEPAGE_BYTES]
            home = str(response.url)
    except (httpx.HTTPError, UnicodeDecodeError) as error:
        log.debug("%s: homepage unreadable: %s", host, error)

    for url in _icon_candidates(html, home):
        payload = await _get_image(client, host, url, small)
        if payload:
            return payload, answered
    return None, answered


async def _fetch_logo(client: httpx.AsyncClient, domain: str) -> bytes | None:
    small = _Small()
    payload, answered = await _from_site(client, domain, small)
    if payload:
        return payload
    # Only a host that never answered is worth a second address; one that served
    # a page served its icons too, and `www` would repeat the same requests.
    if not answered and not domain.startswith("www."):
        payload, _ = await _from_site(client, f"www.{domain}", small)
        if payload:
            return payload
    payload = await _get_image(client, domain, GOOGLE_FAVICON.format(domain=domain), small)
    if payload:
        return payload
    if small.payload:
        log.debug("%s: storing a %dpx mark, nothing larger exists", domain, small.edge)
    return small.payload


@dataclass
class Result:
    attempted: int = 0
    stored: int = 0
    failed: int = 0
    bytes_stored: int = 0
    failures: list[str] = field(default_factory=list)


async def harvest(
    pool: Pool,
    *,
    limit: int | None = None,
    refresh: bool = False,
    retry_failed: bool = False,
) -> Result:
    """Fetch and store a logo for every company that has a domain but no row.

    `refresh` reconsiders companies already recorded, including the failures.
    `retry_failed` reconsiders only the recorded failures (NULL `bytes`), for a
    rerun after the fetch learned a new source.
    """
    if refresh:
        where = ""
    elif retry_failed:
        where = "AND (l.company_id IS NULL OR l.bytes IS NULL)"
    else:
        where = "AND l.company_id IS NULL"
    rows = await pool.fetch(
        f"""
        SELECT c.id, c.domain
        FROM company c
        LEFT JOIN company_logo l ON l.company_id = c.id
        WHERE c.domain IS NOT NULL AND c.domain <> ''
          {where}
        ORDER BY c.id
        {"LIMIT " + str(int(limit)) if limit else ""}
        """
    )

    result = Result(attempted=len(rows))
    if not rows:
        return result

    gate = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        limits=limits,
    ) as client:

        async def one(company_id: int, domain: str) -> tuple[int, str, bytes | None]:
            async with gate:
                # A single site must never fail the run: these are third-party
                # hosts and they fail in ways httpx does not model.
                try:
                    return company_id, domain, await _fetch_logo(client, domain)
                except Exception as error:  # noqa: BLE001
                    log.debug("%s: %s", domain, error)
                    return company_id, domain, None

        tasks = [asyncio.create_task(one(row["id"], row["domain"])) for row in rows]
        done = 0
        for task in asyncio.as_completed(tasks):
            company_id, domain, payload = await task
            await pool.execute(
                """
                INSERT INTO company_logo (company_id, bytes) VALUES ($1, $2)
                ON CONFLICT (company_id) DO UPDATE SET bytes = EXCLUDED.bytes
                """,
                company_id,
                payload,
            )
            if payload:
                result.stored += 1
                result.bytes_stored += len(payload)
            else:
                result.failed += 1
                result.failures.append(domain)
            done += 1
            if done % 200 == 0:
                log.info("%d/%d stored=%d", done, len(rows), result.stored)

    return result


async def harvest_one(
    pool: Pool, company_id: int, *, domain: str | None = None
) -> bytes | None:
    """Fetch and store one company's logo on demand. Returns what was stored.

    The bulk :func:`harvest` skips a company that already has a row, because a
    sweep of thousands must not re-pay a timeout it already paid. This is the
    opposite case -- an operator pointed at one row and asked -- so it always
    refetches and always overwrites, the same as ``--refresh`` for a single id.

    *domain* overrides the stored column so the button works on a domain the
    operator has typed but not yet saved; without it there is nothing to fetch
    and the caller gets ``None`` rather than a request to ``https://None/``.
    """
    target = (domain or "").strip()
    if not target:
        target = await pool.fetchval(
            "SELECT domain FROM company WHERE id = $1", company_id
        ) or ""
    target = store.normalize_domain(target, board_hosts_ok=True)
    if not target:
        return None

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    ) as client:
        try:
            payload = await _fetch_logo(client, target)
        except Exception as error:  # noqa: BLE001 -- third-party hosts fail freely
            log.debug("%s: %s", target, error)
            payload = None

    # The row is written either way: a NULL `bytes` records that this domain
    # was asked and had nothing usable, which is an answer the UI can show.
    await pool.execute(
        """
        INSERT INTO company_logo (company_id, bytes) VALUES ($1, $2)
        ON CONFLICT (company_id) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        company_id,
        payload,
    )
    return payload


async def normalize_stored(pool: Pool) -> tuple[int, int, int]:
    """Re-run `normalize` over rows harvested before it existed.

    Returns (examined, rewritten, bytes saved). Idempotent: a row already at or
    under `MAX_EDGE` normalizes to itself and is not written.
    """
    rows = await pool.fetch(
        "SELECT company_id, bytes FROM company_logo WHERE bytes IS NOT NULL ORDER BY company_id"
    )
    rewritten = saved = 0
    for row in rows:
        original = bytes(row["bytes"])
        shrunk = normalize(original)
        if len(shrunk) >= len(original):
            continue
        await pool.execute(
            "UPDATE company_logo SET bytes = $2 WHERE company_id = $1",
            row["company_id"],
            shrunk,
        )
        rewritten += 1
        saved += len(original) - len(shrunk)
    return len(rows), rewritten, saved


def _env_dsn() -> str | None:
    return os.environ.get("DATABASE_URL")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", help="Postgres DSN; default: $DATABASE_URL")
    parser.add_argument("--limit", type=int, help="stop after this many companies")
    parser.add_argument(
        "--refresh", action="store_true", help="also refetch companies already recorded"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="also refetch companies recorded as failed (no logo), keeping stored logos",
    )
    parser.add_argument(
        "--normalize-stored",
        action="store_true",
        help="downscale already-stored logos and exit, without fetching anything",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or _env_dsn()
    if not dsn:
        raise ValueError("no dsn: pass --dsn or set DATABASE_URL")

    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    # One line per icon fetch across thousands of domains buries the progress log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    pool = await store.connect(dsn)
    try:
        if args.normalize_stored:
            examined, rewritten, saved = await normalize_stored(pool)
            print(
                f"examined={examined} rewritten={rewritten} saved={saved / 1e6:.1f}MB"
            )
            return 0
        result = await harvest(
            pool, limit=args.limit, refresh=args.refresh, retry_failed=args.retry_failed
        )
    finally:
        await pool.close()

    average = result.bytes_stored // result.stored if result.stored else 0
    print(
        f"attempted={result.attempted} stored={result.stored} failed={result.failed} "
        f"total={result.bytes_stored / 1e6:.1f}MB avg={average / 1000:.1f}KB"
    )
    return 0


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
