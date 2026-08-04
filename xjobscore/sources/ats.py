"""Small config-driven adapter for JSON ATS boards.

Add a board in ``ats_configs.py``.  The engine deliberately understands only
the common public-board shape: JSON list/detail endpoints, simple pagination,
and field extraction.  Raw items remain the complete vendor payload.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from functools import partial
from typing import Any

from xjobscore.net import Net, NetError, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record
from xjobscore.sources.ats_configs import CONFIGS

SPECS = CONFIGS  # Compatibility name for callers that previously inspected ats.py.
REGISTRY = CONFIGS
ADAPTERS = CONFIGS

_TOKEN = re.compile(r"{([^}]+)}")


def _dig(value: Any, path: str | None) -> Any:
    if path in (None, "", "."):
        return value
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
        if value is None:
            return None
    return value


def _value(spec: Any, payload: Any, context: dict[str, Any]) -> Any:
    """Resolve literals plus the tiny expression vocabulary used by configs."""
    if isinstance(spec, str):
        if spec.startswith("@"):
            return _dig(payload, spec[1:])
        if spec.startswith("$"):
            return _dig(context, spec[1:])
        return spec
    if isinstance(spec, list):
        return [_value(value, payload, context) for value in spec]
    if not isinstance(spec, dict):
        return spec
    if "fallback" in spec:
        return next(
            (value for value in (_value(part, payload, context) for part in spec["fallback"])
             if value not in (None, "")),
            None,
        )
    if "join" in spec:
        parts = [_value(part, payload, context) for part in spec["join"]]
        return spec.get("sep", ", ").join(str(part) for part in parts if part not in (None, "")) or None
    if "template" in spec:
        return _format(spec["template"], payload, context)
    if "date" in spec:
        value = _value(spec["date"], payload, context)
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    if "epoch_ms" in spec:
        value = _value(spec["epoch_ms"], payload, context)
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc) if value else None
    if "epoch_s" in spec:
        value = _value(spec["epoch_s"], payload, context)
        return datetime.fromtimestamp(value, tz=timezone.utc) if value is not None else None
    if "unescape" in spec:
        value = _value(spec["unescape"], payload, context)
        return html.unescape(value) if value else None
    if "first" in spec:
        values = _value(spec["first"], payload, context) or []
        return values[0] if values else None
    if "rest" in spec:
        values = _value(spec["rest"], payload, context) or []
        return list(values[1:])
    if "lookup" in spec:
        table, key = (_value(value, payload, context) for value in spec["lookup"])
        return table.get(str(key)) if isinstance(table, dict) and key is not None else None
    if "map" in spec:
        rows = _value(spec["map"], payload, context) or []
        if not isinstance(rows, list):
            return []
        values = [_value(spec.get("value", "@"), row, context) for row in rows]
        return [value for value in values if value is not None]
    if "sections" in spec:
        rows = _value(spec["sections"], payload, context) or []
        if spec.get("sort"):
            rows = sorted(rows, key=lambda row: _value(spec["sort"], row, context) or 0)
        return [
            {"heading": _value(spec["heading"], row, context), "html": _value(spec["html"], row, context)}
            for row in rows
        ]
    if "concat" in spec:
        values = _value(spec["concat"], payload, context)
        if not isinstance(values, list):
            values = [values]
        return spec.get("sep", "").join(str(value) for value in values if value not in (None, "")) or None
    if "eq" in spec:
        left, right = (_value(value, payload, context) for value in spec["eq"])
        return left == right
    return {key: _value(value, payload, context) for key, value in spec.items()}


def _format(template: str, payload: Any, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        expression = token if token.startswith(("@", "$")) else f"@{token}"
        value = context.get(token) if token in context else _value(expression, payload, context)
        return "" if value is None else str(value)

    return _TOKEN.sub(replace, template)


def _render(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    return _value(value, None, context)


async def _request(request: dict, feed: Feed, net: Net, context: dict[str, Any], *, first: bool):
    settings = getattr(feed, "config", None) or {}
    template = settings.get("url", request["url"]) if request.get("url_override", True) else request["url"]
    url = _format(template, None, context)
    if request.get("method", "GET") == "POST":
        return await net.post(url, json=_render(request.get("body", {}), context))
    kwargs: dict[str, Any] = {}
    if request.get("params"):
        kwargs["params"] = _render(request["params"], context)
    if first and request.get("conditional", True):
        kwargs["state"] = feed.state
    return await net.get(url, **kwargs)


def _items(body: Any, request: dict) -> list[dict]:
    rows = _dig(body, request.get("items"))
    if not isinstance(rows, list):
        return []
    unwrap = request.get("unwrap")
    return [_dig(row, unwrap) if unwrap else row for row in rows if isinstance(row, dict)]


def _item_id(spec: Any, payload: dict, context: dict[str, Any]) -> str | None:
    value = _value(spec, payload, context)
    return str(value) if value is not None else None


def _record_items(config: dict, rows: list[dict], context: dict[str, Any]) -> list[Item]:
    return [
        Item(external_id=external_id, payload=row)
        for row in rows
        if (external_id := _item_id(config["list"]["id"], row, context)) is not None
    ]


def _more(page: list[dict], rows: list[dict], page_config: dict, context: dict[str, Any]) -> bool:
    total = context.get("total")
    if total is not None:
        return len(rows) < total and bool(page)
    size = page_config.get("size")
    return len(page) >= size if size else bool(page)


async def harvest(name: str, feed: Feed, net: Net) -> Record:
    config = CONFIGS[name]
    list_config = config["list"]
    settings = getattr(feed, "config", None) or {}
    context = {"key": feed.key, "cursor": (list_config.get("page") or {}).get("start", 0)}
    key_parts = config.get("key_parts", ())
    context.update(zip(key_parts, feed.key.split("/", len(key_parts) - 1)))
    context.update(settings)
    try:
        response = await _request(list_config, feed, net, context, first=True)
    except NetError:
        if list_config.get("catch_first_error"):
            return Record(truncated=True)
        raise
    first_status, first_validators = response.status, validators(response)
    if response.not_modified:
        return Record(http_status=304, validators=first_validators)

    body = response.json()
    rows = _items(body, list_config)
    page = rows
    context["envelope"] = {key: _value(value, body, context) for key, value in config.get("envelope", {}).items()}
    page_config = list_config.get("page")
    if page_config and page_config.get("total"):
        context["total"] = _value(page_config["total"], body, context)
    last_status = response.status
    pages = 1
    while page_config and _more(page, rows, page_config, context):
        if pages >= page_config.get("max", float("inf")):
            return Record(
                items=_record_items(config, rows, context), envelope=context["envelope"], truncated=True,
                http_status=first_status, validators=first_validators,
            )
        step = len(page) if page_config.get("step") == "count" else page_config.get("step", page_config.get("size", 1))
        context["cursor"] += step
        try:
            response = await _request(list_config, feed, net, context, first=False)
        except NetError:
            return Record(
                items=_record_items(config, rows, context) if "detail" not in config else [],
                envelope=context["envelope"], truncated=True, http_status=first_status, validators=first_validators,
            )
        last_status = response.status
        body = response.json()
        page = _items(body, list_config)
        rows.extend(page)
        pages += 1

    detail = config.get("detail")
    if not detail:
        return Record(items=_record_items(config, rows, context), envelope=context["envelope"], http_status=last_status if config.get("last_status") else first_status, validators=first_validators)

    items: list[Item] = []
    seen: set[str] = set()
    for row in rows:
        detail_id = _item_id(detail.get("id", list_config["id"]), row, context)
        dedupe = _item_id(detail.get("dedupe"), row, context) if detail.get("dedupe") else detail_id
        if detail_id is None or (dedupe in seen if dedupe else False):
            continue
        if dedupe:
            seen.add(dedupe)
        detail_context = {**context, "id": detail_id}
        try:
            detail_response = await _request(detail, feed, net, detail_context, first=False)
        except NetError:
            return Record(items=items, envelope=context["envelope"], truncated=True, http_status=last_status if config.get("last_status") else first_status, validators=first_validators)
        payload = _dig(detail_response.json(), detail.get("payload"))
        if not isinstance(payload, dict):
            continue
        if detail.get("merge"):
            payload = {**row, **payload}
        external_id = _item_id(detail.get("item_id", list_config["id"]), payload, detail_context) or detail_id
        items.append(Item(external_id=external_id, payload=payload))
    return Record(items=items, envelope=context["envelope"], http_status=last_status if config.get("last_status") else first_status, validators=first_validators)


def parse(name: str, feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    config = CONFIGS[name]
    settings = getattr(feed, "config", None) or {}
    context = {"key": feed.key, "envelope": envelope}
    context.update(zip(config.get("key_parts", ()), feed.key.split("/", len(config.get("key_parts", ())) - 1)))
    context.update(settings)
    fields = {key: _value(value, item.payload, context) for key, value in config.get("fields", {}).items()}
    fields.setdefault("title", "")
    fields.setdefault("url_posting", "")
    fields.setdefault("fidelity", "origin")
    fields.setdefault("state", "open")
    fields.setdefault("published_at_precision", "unknown")
    fields.setdefault("locations_extra", [])
    fields.setdefault("content_sections", [])
    fields["source_extra"] = {
        key: _value(value, item.payload, context) for key, value in config.get("extra", {}).items()
    }
    return [Posting(external_id=item.external_id, **fields)]


def harvester(name: str):
    return partial(harvest, name)


def parser(name: str):
    return partial(parse, name)


def get_adapter(name: str) -> dict:
    return CONFIGS[name]


get = get_adapter
