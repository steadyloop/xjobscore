"""Consolidate Workable's repeated location rows without changing its fetch."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace

from xjobscore.net import Net
from xjobscore.records import Canonical, Feed, Item, Posting, Record
from xjobscore.sources import json_api

_LOCATION_FIELDS = {"city", "state", "country", "locations"}
_ROWS_KEY = "_workable_location_rows"


def _content(row: dict) -> str:
    return json.dumps(
        {key: value for key, value in row.items() if key not in _LOCATION_FIELDS},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _eligible(external_id: str, rows: list) -> bool:
    if len(rows) < 2 or any(
        not isinstance(row, dict)
        or _ROWS_KEY in row or "_probe" in row
        or str(row.get("shortcode", "")) != external_id
        for row in rows
    ):
        return False
    content = _content(rows[0])
    return all(_content(row) == content for row in rows[1:])


async def harvest(feed: Feed, net: Net) -> Record:
    record = await json_api.harvest("workable", feed, net)
    groups: dict[str, list[tuple[int, Item]]] = defaultdict(list)
    for index, item in enumerate(record.items):
        groups[item.external_id].append((index, item))
    merged: dict[str, tuple[int, Item]] = {}
    for external_id, group in groups.items():
        rows = [item.payload for _, item in group]
        if all(item.kind == "json" for _, item in group) and _eligible(external_id, rows):
            index, last = group[-1]
            # Sequential processing previously left the final row as primary.
            payload = {**last.payload, _ROWS_KEY: {"version": 1, "rows": rows}}
            merged[external_id] = (index, replace(last, payload=payload))
    if not merged:
        return record
    items = []
    for index, item in enumerate(record.items):
        replacement = merged.get(item.external_id)
        if replacement is None:
            items.append(item)
        elif index == replacement[0]:
            items.append(replacement[1])
    # Transport, completeness and diagnostic fields describe the original fetch.
    return replace(record, items=items)


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    postings = json_api.parse("workable", feed, item, envelope)
    payload = item.payload
    metadata = payload.get(_ROWS_KEY) if isinstance(payload, dict) else None
    if not isinstance(metadata, dict) or metadata.get("version") != 1:
        return postings
    rows = metadata.get("rows")
    if not isinstance(rows, list) or not _eligible(item.external_id, rows):
        return postings
    if {key: value for key, value in payload.items() if key != _ROWS_KEY} != rows[-1]:
        return postings

    fields = json_api.CONFIGS["workable"]["fields"]
    locations: list[str] = []
    extras: list[str] = []
    for row in rows:
        value = json_api._value(fields["location_raw"], row, {})
        if value and value not in locations:
            locations.append(value)
        for value in json_api._value(fields["locations_extra"], row, {}):
            if value not in extras:
                extras.append(value)
        structured = row.get("locations")
        for location in structured if isinstance(structured, list) else []:
            if isinstance(location, dict):
                value = json_api._value(
                    {"join": ["@city", "@region", "@country"]}, location, {},
                )
                if value and value not in locations:
                    locations.append(value)
    return [
        replace(
            posting,
            location_normalization_extra=[
                value for value in locations if value != posting.location_raw
            ],
            locations_extra=extras,
        ) if isinstance(posting, Posting) else posting
        for posting in postings
    ]
