"""Canonical feed identities shared by discovery and PostgreSQL."""

from __future__ import annotations

import hashlib

__all__ = [
    "canonical_feed_source", "canonical_feed_key", "canonical_feed_identity",
    "feed_ref",
]


_SOURCE_RENAMES = {"workday": "workday-list"}


def canonical_feed_source(source: str) -> str:
    """Return the current adapter name for a persisted source label.

    Workday's former detail adapter and its listing-only replacement address
    the same tenant endpoints.  The detail leg is gone, so its old source name
    is an input alias rather than a second database identity.
    """
    return _SOURCE_RENAMES.get(source, source)


def canonical_feed_key(source: str, key: str) -> str:
    """Return the one database key for a vendor endpoint.

    Workday accepts tenant, host-shard, and site path casing interchangeably.
    Discovery therefore sees variants such as ``Cisco_Careers`` and
    ``cisco_careers`` even though both return the same board. PostgreSQL's
    ordinary text uniqueness is case-sensitive, so normalize this adapter at
    every ingestion boundary before identity reaches either ``token`` or
    ``feed``.
    """
    source = canonical_feed_source(source)
    return key.lower() if source == "workday-list" else key


def canonical_feed_identity(source: str, key: str) -> tuple[str, str]:
    source = canonical_feed_source(source)
    return source, canonical_feed_key(source, key)


def feed_ref(source: str, key: str) -> str:
    """Return the stable public identity of a feed."""
    return hashlib.sha256(f"{source}\0{key}".encode()).hexdigest()
