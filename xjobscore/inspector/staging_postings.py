"""Read-only staging-payload queries -- the legacy "Postings (Old)" page.

Superseded by ``postings.py``, which reads the gold ``public.job_posting``
table. This module still answers ``/api/postings-old`` for the archived page
at ``/postings-old``, browsing the latest staged payload per feed.
"""

from __future__ import annotations

import json
import re
from typing import Any

INTERNSHIP_SIGNAL_RE = re.compile(
    r"(?<![a-z])(intern(?:ship)?s?|co[- ]?ops?|fellow(?:ship)?s?|extern(?:ship)?s?)(?![a-z])",
    re.IGNORECASE,
)
US_COUNTRY_VALUES = {"us", "usa", "unitedstates", "unitedstatesofamerica"}
US_LOCATION_RE = re.compile(
    r"\b(?:united states(?: of america)?|u\.?s\.?a?\.?|usa|"
    r"san francisco|new york(?: city)?|nyc|austin|boston|chicago|"
    r"dallas|seattle|palo alto|mountain view|los angeles|san mateo|"
    r"san carlos|atlanta|oxnard|denver|washington(?:,? d\.?c\.?)?|"
    r"san diego|berkeley|sunnyvale|philadelphia|houston|miami)\b",
    re.IGNORECASE,
)
FOREIGN_LOCATION_RE = re.compile(
    r"\b(?:canada|mexico|brazil|argentina|chile|colombia|peru|"
    r"united kingdom|uk|england|scotland|wales|ireland|france|germany|"
    r"netherlands|belgium|spain|portugal|italy|switzerland|austria|"
    r"poland|ukraine|czechia|denmark|sweden|norway|finland|iceland|"
    r"estonia|latvia|lithuania|romania|hungary|croatia|bulgaria|greece|"
    r"turkey|israel|india|pakistan|bangladesh|singapore|malaysia|"
    r"indonesia|thailand|vietnam|philippines|china|japan|south korea|"
    r"korea|taiwan|australia|new zealand|south africa|egypt|nigeria|"
    r"kenya|ghana|europe|emea|apac|apj|latam|asia pacific|middle east|"
    r"london|berlin|munich|paris|amsterdam|dublin|lisbon|madrid|barcelona|"
    r"zurich|stockholm|copenhagen|oslo|warsaw|kyiv|lviv|prague|vienna|"
    r"bucharest|sofia|tallinn|riga|vilnius|belgrade|zagreb|helsinki|"
    r"brussels|toronto|vancouver|montreal|calgary|ottawa|mexico city|"
    r"sao paulo|rio de janeiro|buenos aires|bogota|lima|santiago|"
    r"bengaluru|bangalore|hyderabad|mumbai|new delhi|gurgaon|noida|"
    r"chennai|pune|tokyo|osaka|seoul|taipei|hong kong|shanghai|beijing|"
    r"dubai|abu dhabi|tel aviv|jerusalem|auckland|melbourne|sydney|"
    r"perth|brisbane|cape town|johannesburg|manila|jakarta|bangkok|"
    r"ho chi minh|hanoi)\b",
    re.IGNORECASE,
)


def _dig(value: Any, *parts: str) -> Any:
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _first(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _dig(payload, *path)
        if value not in (None, "", []):
            return value
    return None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _location(payload: dict[str, Any]) -> str | None:
    value = _first(
        payload,
        ("location",),
        ("location", "name"),
        ("categories", "location"),
        ("city",),
    )
    if isinstance(value, dict):
        return _string(
            _first(value, ("name",), ("city",), ("label",), ("fullLocation",))
        )
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, "")) or None
    return _string(value)


def summarize_payload(payload: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    title = _string(
        _first(
            payload,
            ("title",), ("jobTitle",), ("job_title",), ("positionTitle",),
            ("postingTitle",), ("name",), ("text",),
        )
    ) or "Untitled job"
    employment_type = _string(
        _first(
            payload,
            ("employmentType",), ("employment_type",),
            ("categories", "commitment"), ("type", "name"),
        )
    )
    url = _string(
        _first(
            payload,
            ("jobUrl",), ("url",), ("hostedUrl",), ("absolute_url",),
            ("postingUrl",), ("externalUrl",), ("applyUrl",),
        )
    )
    return {
        "title": title,
        "location": _location(payload),
        "employment_type": employment_type,
        "url": url,
        "listed": payload.get("isListed") if isinstance(payload.get("isListed"), bool) else None,
    }


def _normalized_employment_type(summary: dict[str, Any]) -> str:
    return re.sub(r"[^a-z]", "", (summary["employment_type"] or "").lower())


def _address_country(address: Any) -> str | None:
    if not isinstance(address, dict):
        return None
    postal_address = address.get("postalAddress")
    if isinstance(postal_address, dict):
        address = postal_address
    return _string(address.get("addressCountry"))


def _location_text(payload: dict[str, Any]) -> str:
    values: list[str] = []
    location = payload.get("location")
    if isinstance(location, str):
        values.append(location)
    elif isinstance(location, list):
        values.extend(str(item) for item in location if item not in (None, ""))
    for secondary in payload.get("secondaryLocations") or []:
        if isinstance(secondary, dict) and secondary.get("location"):
            values.append(str(secondary["location"]))
    return " | ".join(values)


def _is_explicitly_foreign(payload: dict[str, Any]) -> bool:
    countries = [_address_country(payload.get("address"))]
    for location in payload.get("secondaryLocations") or []:
        if isinstance(location, dict):
            countries.append(_address_country(location.get("address")))
    normalized = {
        re.sub(r"[^a-z]", "", country.lower())
        for country in countries
        if country
    }
    if normalized.intersection(US_COUNTRY_VALUES):
        return False
    location_text = _location_text(payload)
    if US_LOCATION_RE.search(location_text):
        return False
    if normalized:
        return True
    return bool(FOREIGN_LOCATION_RE.search(location_text))


def _has_internship_signal(summary: dict[str, Any], payload: dict[str, Any]) -> bool:
    description = _string(
        _first(payload, ("descriptionPlain",), ("descriptionHtml",))
    ) or ""
    return bool(INTERNSHIP_SIGNAL_RE.search(summary["title"])) or bool(
        INTERNSHIP_SIGNAL_RE.search(description)
    )


def _matches(
    summary: dict[str, Any], payload: dict[str, Any], mode: str, *, us_only: bool
) -> bool:
    if summary["listed"] is False:
        return False
    if us_only and _is_explicitly_foreign(payload):
        return False
    if mode == "all":
        return True
    employment = _normalized_employment_type(summary)
    if mode == "intern_explicit":
        return employment == "intern"
    if mode == "intern_implicit":
        return employment not in {"intern", "fulltime"} and _has_internship_signal(
            summary, payload
        )
    if mode == "full_time_internship":
        return employment == "fulltime" and _has_internship_signal(summary, payload)
    if mode == "exclude_full_time":
        return employment != "fulltime"
    raise ValueError(f"unknown posting filter mode: {mode}")


def _searchable(summary: dict[str, Any], source: str, key: str, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        value or ""
        for value in (source, key, summary["title"], summary["location"], summary["employment_type"])
    ).lower()
    return query.lower() in haystack


async def query(
    store,
    *,
    source: str | None = None,
    search: str = "",
    mode: str = "exclude_full_time",
    us_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return compact filtered rows from the latest persisted run per feed.

    Every filter here inspects the payload, so the scan is unavoidable; it is
    streamed through a server-side cursor rather than buffered, because one
    ATS source can hold hundreds of thousands of staged items.
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    staging = store.staging
    pool = await store.pool()
    matched: list[dict[str, Any]] = []
    async with pool.acquire() as connection, connection.transaction():
        sources = [
            row["source"]
            for row in await connection.fetch(
                f"SELECT DISTINCT source FROM {staging}.fetched_feed ORDER BY source"
            )
        ]
        if source == "*":
            source = None
        elif source is None:
            source = "ashby" if "ashby" in sources else None
        cursor = connection.cursor(
            f"""
            SELECT r.result_id, r.source, r.feed_key, r.fetched_at AS finished_at,
                   i.item_index, i.external_id, i.payload
            FROM {staging}.fetched_feed AS r
            JOIN {staging}.fetched_feed_posting AS i
              ON i.source = r.source AND i.feed_key = r.feed_key
            WHERE ($1::text IS NULL OR r.source = $1)
            ORDER BY r.fetched_at DESC, r.source, r.feed_key, i.item_index
            """,
            source,
        )
        async for row in cursor:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            summary = summarize_payload(payload)
            if not _matches(summary, payload, mode, us_only=us_only) or not _searchable(
                summary, row["source"], row["feed_key"], search.strip()
            ):
                continue
            matched.append(
                {
                    "result_id": row["result_id"],
                    "source": row["source"],
                    "feed_key": row["feed_key"],
                    "finished_at": row["finished_at"].isoformat(),
                    "item_index": row["item_index"],
                    "external_id": row["external_id"],
                    **summary,
                }
            )
    return {
        "source": source,
        "sources": sources,
        "mode": mode,
        "us_only": us_only,
        "search": search,
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "rows": matched[offset : offset + limit],
    }


async def detail(store, result_id: str, item_index: int) -> dict[str, Any] | None:
    """Read one stored payload by its run id and position."""
    pool = await store.pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            f"""
            SELECT r.source, r.feed_key, r.fetched_at AS finished_at,
                   i.external_id, i.kind, i.payload, i.raw_document
            FROM {store.staging}.fetched_feed AS r
            JOIN {store.staging}.fetched_feed_posting AS i
              ON i.source = r.source AND i.feed_key = r.feed_key
            WHERE r.result_id = $1 AND i.item_index = $2
            """,
            result_id,
            item_index,
        )
    if row is None:
        return None
    return {
        "source": row["source"],
        "feed_key": row["feed_key"],
        "finished_at": row["finished_at"].isoformat(),
        "external_id": row["external_id"],
        "kind": row["kind"],
        "payload": json.loads(row["payload"]),
        "raw_document": json.loads(row["raw_document"]),
    }
