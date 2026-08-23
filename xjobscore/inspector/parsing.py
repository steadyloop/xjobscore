"""Pure parse preview mirroring silver.process, with no database writes.

The inspector re-runs a source's registered ``parse`` over freshly harvested
items so both pipeline stages can be verified from one fetch. Classification
mirrors silver.process exactly -- unsupported / secondhand / company fallback
-- but company resolution consults a PostgreSQL alias snapshot, and per-item
parser exceptions are captured rather than
propagated: one malformed payload must not hide the other results.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any

from xjobscore import gold, sources
from xjobscore.records import Fact, Feed, Item, Label, Posting, Target

__all__ = ["parse_record"]

SUMMARY_KEYS = (
    "items_parsed",
    "item_errors",
    "postings",
    "targets",
    "labels",
    "facts",
    "unsupported",
    "resolved",
    "fallback_company",
    "would_park",
)

AliasLookup = Callable[[str], tuple[int, str] | None]


def _company_resolution(
    feed: Feed, posting: Posting, resolve_alias: AliasLookup
) -> dict[str, Any]:
    """Preview silver._company_id: resolved, feed fallback, or parked."""
    if posting.company_raw:
        resolved = resolve_alias(posting.company_raw)
        if resolved is not None:
            company_id, company_name = resolved
            return {
                "status": "resolved",
                "company_id": company_id,
                "company_name": company_name,
                "company_raw": posting.company_raw,
            }
    if feed.type in {"ATS", "Direct"} and feed.company_id is not None:
        return {
            "status": "fallback",
            "company_id": feed.company_id,
            "company_name": feed.company_name,
            "company_raw": posting.company_raw,
        }
    return {"status": "would_park", "company_raw": posting.company_raw}


def _canonical_entry(
    feed: Feed,
    canonical: Any,
    resolve_alias: AliasLookup,
    summary: dict[str, int],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": type(canonical).__name__,
        "data": dataclasses.asdict(canonical),
    }
    if not isinstance(canonical, Posting):
        # silver counts non-Posting outputs as unsupported; Targets, Labels
        # and Facts are still rendered in full -- they are worth inspecting.
        summary["unsupported"] += 1
        key = {Target: "targets", Label: "labels", Fact: "facts"}.get(type(canonical))
        if key:
            summary[key] += 1
        entry["classification"] = "unsupported"
        return entry
    if canonical.fidelity != "origin":
        entry["classification"] = "secondhand"
        return entry

    summary["postings"] += 1
    entry["classification"] = "posting"
    resolution = _company_resolution(feed, canonical, resolve_alias)
    summary[
        {"resolved": "resolved", "fallback": "fallback_company", "would_park": "would_park"}[
            resolution["status"]
        ]
    ] += 1
    entry["company_resolution"] = resolution
    try:
        entry["title_normalized"] = gold.normalize_title(canonical.title)
    except (AttributeError, TypeError):
        # normalize_title requires a canonical string title. The
        # preview reports it as a data problem instead of failing the run.
        entry["title_normalized"] = None
        entry["title_warning"] = (
            f"normalize_title failed: title={canonical.title!r} is not a string"
        )
    return entry


def parse_record(
    feed: Feed,
    items: Sequence[Item],
    envelope: dict,
    resolve_alias: AliasLookup,
) -> dict[str, Any]:
    """Parse harvested items and classify every canonical, silver-style.

    Returns ``{"status", "summary", "items"}`` where status is ``ok`` or
    ``no_parser``. Sync and CPU-bound by design -- call via asyncio.to_thread.
    """
    summary = dict.fromkeys(SUMMARY_KEYS, 0)
    if feed.source not in sources.PARSERS:
        return {"status": "no_parser", "summary": summary, "items": []}

    parse = sources.parser(feed.source)
    parsed_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row: dict[str, Any] = {
            "index": index,
            "external_id": item.external_id,
            "status": "ok",
            "error_class": None,
            "error_detail": None,
            "canonicals": [],
        }
        try:
            canonicals = parse(feed, item, envelope)
        except Exception as exc:  # One bad payload must not hide the rest.
            summary["item_errors"] += 1
            row["status"] = "error"
            row["error_class"] = type(exc).__name__
            row["error_detail"] = str(exc)
        else:
            if canonicals:
                summary["items_parsed"] += 1
            row["canonicals"] = [
                _canonical_entry(feed, canonical, resolve_alias, summary)
                for canonical in canonicals
            ]
        parsed_items.append(row)
    return {"status": "ok", "summary": summary, "items": parsed_items}
