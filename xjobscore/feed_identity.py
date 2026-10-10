"""Canonical feed identities shared by discovery and PostgreSQL."""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "canonical_feed_source", "canonical_feed_key", "canonical_feed_identity",
    "feed_ref",
]


_SOURCE_RENAMES = {"workday-list": "workday"}


def canonical_feed_source(source: str) -> str:
    """Return the current adapter name for a persisted source label.

    ``workday-list`` was the listing-only adapter's name while the detail leg
    was retired.  The leg is back -- gated, not ungated -- so the adapter is
    ``workday`` again and the interim name is an input alias rather than a
    second database identity.
    """
    return _SOURCE_RENAMES.get(source, source)


# Vendors whose endpoint ignores token casing, so two spellings name one board
# and must not become two rows. Every entry was checked against the live API
# rather than assumed -- the trailing comment records the probe that settled it.
#
# `comeet` is deliberately absent: its key is `slug/board-id` (the API token
# lives in token.config), and while the API does ignore board-id case
# (arbe/C6.001 and arbe/c6.001 return identical payloads), the slug half does
# not fold the same way across renames. Board-level dedup lives in
# console/tokens.py `_BOARD`, keyed on the upper-cased board id.
CASE_INSENSITIVE_SOURCES = frozenset({
    "workday",          # Cisco_Careers / cisco_careers -- one board
    "greenhouse",       # Duolingo / duolingo -- both 88 jobs
    "ashby",            # Ramp 145, Notion 131, Acorns 9 -- identical each way
    "smartrecruiters",  # Solidigm, Canva, LinkedIn3, AbbVie -- all 200 folded
    "oracle",           # CX_1 / cx_1 -- both TotalJobsCount 26
    "ukg",              # UNI1076UNFI / uni1076unfi -- both 200
    "bullhorn",         # 2WXP1S / 2wxp1s -- byte-identical payload
    "recruitcrm",       # both 200
    "workable",         # Hampr 404 / hampr 200 -- lowercase is the live form
    "dayforce",         # ROHCGF / rohcgf -- both 200
    "simplify",         # a GitHub owner/repo path; both spellings 200
})

_AVATURE_LOCALE = re.compile(r"[a-z]{2}_[a-z]{2}")


def _avature_key(key: str) -> str:
    """`company[/xx_YY]/board`, folded the way Avature itself folds it.

    Company and board ignore case (bupaanz/CareersAU = careersau, same jobs)
    but the locale does not -- ja_JP is a board, ja_jp a 404 (2026-10-06) --
    so it keeps its `xx_YY` form. A bare key has always meant the `careers`
    board, so `company/careers` is stored bare rather than as a second feed.
    Kept in step with keyscan's `_canonical_token`.
    """
    parts = key.lower().split("/")
    if len(parts) == 3 and _AVATURE_LOCALE.fullmatch(parts[1]):
        parts[1] = parts[1][:3] + parts[1][3:].upper()
    if parts[1:] == ["careers"]:
        parts = parts[:1]
    return "/".join(parts)

# Verified case-SENSITIVE, and folding them would break live feeds:
#   lever     11 of 12 live mixed-case slugs 404 when lowered (BDG 12 jobs, bdg gone)
#   fountain  the trailing country segment is literal -- US.json 200, us.json 404


def canonical_feed_key(source: str, key: str) -> str:
    """Return the one database key for a vendor endpoint.

    Workday accepts tenant, host-shard, and site path casing interchangeably.
    Discovery therefore sees variants such as ``Cisco_Careers`` and
    ``cisco_careers`` even though both return the same board. PostgreSQL's
    ordinary text uniqueness is case-sensitive, so normalize these adapters at
    every ingestion boundary before identity reaches either ``token`` or
    ``feed``.
    """
    source = canonical_feed_source(source)
    if source == "avature":
        return _avature_key(key)
    if source == "ashby":
        # Board names hold spaces (`Citizen Health`); discovered URLs carry
        # them as `%20`. One spelling, decoded: the API takes either, and the
        # key doubles as `company_raw`. Kept in step with keyscan.
        key = key.replace("%20", " ")
    return key.lower() if source in CASE_INSENSITIVE_SOURCES else key


def canonical_feed_identity(source: str, key: str) -> tuple[str, str]:
    source = canonical_feed_source(source)
    return source, canonical_feed_key(source, key)


def feed_ref(source: str, key: str) -> str:
    """Return the stable public identity of a feed."""
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()
