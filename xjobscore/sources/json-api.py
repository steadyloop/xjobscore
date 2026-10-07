"""Small config-driven adapter for public JSON API boards.

Add a board in ``json-api-configs.py``.  The engine deliberately understands only
the common public-board shape: JSON list/detail endpoints, simple pagination,
and field extraction.  Raw items remain the complete vendor payload.
"""

from __future__ import annotations

import asyncio
import html
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from importlib import import_module
from typing import Any

from xjobscore import classify
from xjobscore.probe import parse_tombstone
from xjobscore.net import Net, NetError, etag
from xjobscore.records import Canonical, Feed, Item, Posting, Record
CONFIGS = import_module(".json-api-configs", __package__).CONFIGS

SPECS = CONFIGS
REGISTRY = CONFIGS
ADAPTERS = CONFIGS

_TOKEN = re.compile(r"{([^}]+)}")


@dataclass(frozen=True, slots=True)
class JsonApiHarvestOptions:
    """One-run overrides for controlled pagination and detail reuse.

    The registered harvester still accepts only ``(feed, net)``. The inspector
    may override request widths; the production scheduler supplies prior items
    and refresh timing through ``sources.reusing``.
    """

    page_step: int | None = None
    page_concurrency: int | None = None
    detail_concurrency: int | None = None
    revision_fields: tuple[str, ...] = ()
    list_revision_fields: tuple[str, ...] = ()
    detail_revision_fields: tuple[str, ...] = ()
    prior_items: Mapping[str, Item] | None = None
    force_full: bool = False
    now: datetime | None = None


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
        if not value:
            return None
        if spec.get("fmt"):
            return datetime.strptime(value, spec["fmt"])
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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
        if "where" in spec:
            # A tenant-defined name/value bag (Greenhouse `metadata`) carries its
            # key in a sibling field rather than in the path, so selecting one
            # entry needs a predicate rather than a deeper `@path`. The predicate
            # is an ordinary expression resolved against the row.
            rows = [row for row in rows if _value(spec["where"], row, context)]
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
    if "classify" in spec:
        # An ordered rule table, first match wins. Each rule reads one already
        # resolved field and tests it on whole-word boundaries; a rule whose
        # `not` pattern matches is skipped rather than ending the scan, so a
        # documented false positive falls through to the next signal.
        for rule in spec["classify"]:
            text = _value(rule["from"], payload, context)
            if not isinstance(text, str) or not text:
                continue
            if "not" in rule and classify.hit(text, rule["not"]):
                continue
            if classify.hit(text, rule["re"]):
                return rule["is"]
        return spec.get("default")
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


async def _request(
    request: dict, feed: Feed, net: Net, context: dict[str, Any], *, first: bool, concurrency: int = 1
):
    settings = getattr(feed, "config", None) or {}
    template = settings.get("url", request["url"]) if request.get("url_override", True) else request["url"]
    url = _format(template, None, context)
    # Headers are the one thing every value in a config is already resolved
    # through _render for: $-prefixed values pull from feed.config (an API
    # key, a contact email), same as `params` and `body` do.
    headers = _render(request["headers"], context) if request.get("headers") else None
    # Passed through only when raised above the default so a fake net without
    # the kwarg keeps working, same as docxtract.
    extra = {"concurrency": concurrency} if concurrency > 1 else {}
    if request.get("method", "GET") == "POST":
        return await net.post(url, json=_render(request.get("body", {}), context), headers=headers, **extra)
    kwargs: dict[str, Any] = {**extra}
    if request.get("params"):
        kwargs["params"] = _render(request["params"], context)
    if headers:
        kwargs["headers"] = headers
    if first and request.get("conditional", True):
        kwargs["etag"] = feed.etag
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


def _revision(payload: dict, fields: tuple[str, ...]) -> tuple[Any, ...] | None:
    """Return a complete revision tuple; an absent component is never reusable."""
    values = tuple(_dig(payload, field) for field in fields)
    return None if not fields or any(value in (None, "") for value in values) else values


def _more(page: list[dict], rows: list[dict], page_config: dict, context: dict[str, Any]) -> bool:
    total = context.get("total")
    if total is not None:
        return len(rows) < total and bool(page)
    size = page_config.get("size")
    return len(page) >= size if size else bool(page)


def _parallel_eligible(page_config: dict, context: dict[str, Any]) -> bool:
    """Fan-out needs a known integer total, a numeric cursor, and fixed integer
    `step` *and* `size`.

    The two are different quantities and only coincide for offset-style boards:
    `step` advances the cursor, `size` is how many items a page carries. A
    page-number board (apple: step 1, size 20) needs both to compute how many
    requests are outstanding, so a config missing either stays sequential.
    """
    step, size = page_config.get("step"), page_config.get("size")
    return (
        isinstance(context.get("total"), int)
        and isinstance(step, int) and step > 0
        and isinstance(size, int) and size > 0
        and isinstance(context.get("cursor"), int)
    )


async def _paginate_parallel(
    list_config: dict, feed: Feed, net: Net, context: dict[str, Any], page_config: dict,
    rows: list[dict], *, concurrency: int
) -> tuple[str, int | None, list[dict]]:
    """Fetch every remaining page at once, bounded by Net's semaphore.

    Extends `rows` in place, in page order. Returns (outcome, last_status,
    last_page): outcome is "done", "capped" (`max` bit with more remaining),
    or "error" (a page failed; rows holds the contiguous prefix of successes).

    How many pages remain is a question about items per page (`size`); where
    each one starts is a question about the cursor (`step`). They are equal for
    an offset board and wildly different for a page-number one, so counting off
    `step` would ask apple for 4,564 pages instead of 229.
    """
    total, step, start = context["total"], page_config["step"], context["cursor"]
    n_more = math.ceil(max(0, total - len(rows)) / page_config["size"])
    max_pages = page_config.get("max")
    capped = max_pages is not None and n_more > max_pages - 1
    if capped:
        n_more = max_pages - 1
    offsets = [start + step * (i + 1) for i in range(n_more)]
    results = await asyncio.gather(
        *(
            _request(list_config, feed, net, {**context, "cursor": offset},
                     first=False, concurrency=concurrency)
            for offset in offsets
        ),
        return_exceptions=True,
    )
    last_status: int | None = None
    page = rows
    # gather preserves argument order, so this walk is offset order. Bodies
    # are only parsed up to the first failure; a .json() error propagates the
    # same way it does from the sequential loop.
    for result in results:
        if isinstance(result, NetError):
            return "error", last_status, page
        if isinstance(result, BaseException):
            raise result
        last_status = result.status
        page = _items(result.json(), list_config)
        rows.extend(page)
    return ("capped" if capped and bool(page) and len(rows) < total else "done"), last_status, page


def _facet_dimensions(body: dict, partition: dict) -> list[tuple[str, list[tuple[str, int]]]]:
    """Flatten Workday-style nested facets into usable partition dimensions."""
    root = _dig(body, partition.get("facets", "facets"))
    dimensions: list[tuple[str, list[tuple[str, int]]]] = []

    def walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            values = node.get("values")
            usable = [
                (str(value["id"]), int(value["count"]))
                for value in values or []
                if isinstance(value, dict)
                and value.get("id") not in (None, "")
                and isinstance(value.get("count"), int)
                and value["count"] > 0
            ]
            parameter = node.get("facetParameter")
            if parameter and usable:
                dimensions.append((str(parameter), usable))
            walk(values)

    walk(root)
    return dimensions


def _facet_groups(
    values: list[tuple[str, int]], window: int
) -> list[tuple[list[str], int]] | None:
    """First-fit-decreasing bins, returned in stable vendor facet order."""
    if not values:
        return None
    indexed = [(index, value_id, count) for index, (value_id, count) in enumerate(values)]
    bins: list[list[tuple[int, str, int]]] = []
    totals: list[int] = []
    for entry in sorted(indexed, key=lambda row: (-row[2], row[0])):
        if entry[2] >= window:
            bins.append([entry])
            totals.append(entry[2])
            continue
        for index, total in enumerate(totals):
            if total < window and total + entry[2] <= window:
                bins[index].append(entry)
                totals[index] += entry[2]
                break
        else:
            bins.append([entry])
            totals.append(entry[2])
    packed = sorted(
        ((sorted(group), total) for group, total in zip(bins, totals)),
        key=lambda pair: pair[0][0][0],
    )
    return [([value_id for _, value_id, _ in group], total) for group, total in packed]


async def _complete_list_query(
    list_config: dict,
    feed: Feed,
    net: Net,
    context: dict[str, Any],
    page_config: dict,
    response: Any,
    body: dict,
    *,
    concurrency: int,
) -> tuple[list[dict], int, bool]:
    """Finish one already-started list query and retain its stable page order."""
    rows = _items(body, list_config)
    page = rows
    query_context = dict(context)
    if page_config.get("total"):
        query_context["total"] = _value(page_config["total"], body, query_context)
    last_status = response.status
    pages = 1
    if (
        concurrency > 1
        and _parallel_eligible(page_config, query_context)
        and _more(page, rows, page_config, query_context)
    ):
        outcome, parallel_status, _ = await _paginate_parallel(
            list_config, feed, net, query_context, page_config, rows, concurrency=concurrency
        )
        return rows, parallel_status or last_status, outcome != "done"
    while _more(page, rows, page_config, query_context):
        if pages >= page_config.get("max", float("inf")):
            return rows, last_status, True
        step = (
            len(page)
            if page_config.get("step") == "count"
            else page_config.get("step", page_config.get("size", 1))
        )
        query_context["cursor"] += step
        try:
            response = await _request(
                list_config, feed, net, query_context, first=False, concurrency=concurrency
            )
        except NetError:
            return rows, last_status, True
        last_status = response.status
        page = _items(response.json(), list_config)
        rows.extend(page)
        pages += 1
    return rows, last_status, False


async def _partition_capped_list(
    list_config: dict,
    feed: Feed,
    net: Net,
    context: dict[str, Any],
    page_config: dict,
    body: dict,
    partition: dict,
    *,
    concurrency: int,
    used: frozenset[str] = frozenset(),
) -> tuple[list[dict], int | None, bool, int]:
    """Split a capped Workday result window into facet-complete queries."""
    window = int(partition["window"])
    reported_total = _value(page_config.get("total"), body, context)
    candidates: list[tuple[int, int, str, list[tuple[list[str], int]]]] = []
    for parameter, values in _facet_dimensions(body, partition):
        if parameter in used or sum(count for _, count in values) < int(reported_total or 0):
            continue
        groups = _facet_groups(values, window)
        if groups:
            candidates.append((len(groups), sum(count for _, count in values), parameter, groups))
    if not candidates:
        return [], None, True, 0
    _, _, parameter, groups = min(candidates, key=lambda row: (row[0], row[1], row[2]))

    async def branch(value_ids: list[str], expected: int) -> tuple[list[dict], int, bool, int]:
        applied = {key: list(value) for key, value in context.get("appliedFacets", {}).items()}
        applied[parameter] = value_ids
        branch_context = {
            **context,
            "cursor": page_config.get("start", 0),
            "appliedFacets": applied,
        }
        try:
            response = await _request(
                list_config, feed, net, branch_context, first=False, concurrency=concurrency
            )
        except NetError:
            return [], 0, True, 1
        branch_body = response.json()
        actual = _value(page_config.get("total"), branch_body, branch_context)

        # A grouped facet whose total differs from the sum of its members is
        # overlapping. Individual values are still exhaustive and dedupe later.
        if len(value_ids) > 1 and actual != expected:
            value_counts = dict(
                next(values for name, values in _facet_dimensions(body, partition) if name == parameter)
            )
            pieces = await asyncio.gather(
                *(branch([value_id], value_counts[value_id]) for value_id in value_ids)
            )
            return _merge_partition_results(pieces, list_config, context, extra_queries=1)

        if isinstance(actual, int) and actual >= window and actual != expected:
            nested_rows, nested_status, truncated, count = await _partition_capped_list(
                list_config,
                feed,
                net,
                branch_context,
                page_config,
                branch_body,
                partition,
                concurrency=concurrency,
                used=used | {parameter},
            )
            return nested_rows, nested_status or response.status, truncated, count + 1
        rows, status, truncated = await _complete_list_query(
            list_config,
            feed,
            net,
            branch_context,
            page_config,
            response,
            branch_body,
            concurrency=concurrency,
        )
        return rows, status, truncated, 1

    return _merge_partition_results(
        await asyncio.gather(*(branch(value_ids, expected) for value_ids, expected in groups)),
        list_config,
        context,
    )


def _merge_partition_results(
    results: Any,
    list_config: dict,
    context: dict[str, Any],
    *,
    extra_queries: int = 0,
) -> tuple[list[dict], int | None, bool, int]:
    rows: list[dict] = []
    seen: set[str] = set()
    status: int | None = None
    truncated = False
    query_count = extra_queries
    for part_rows, part_status, part_truncated, part_count in results:
        status = part_status or status
        truncated = truncated or part_truncated
        query_count += part_count
        for row in part_rows:
            external_id = _item_id(list_config["id"], row, context)
            if external_id is None or external_id in seen:
                continue
            seen.add(external_id)
            rows.append(row)
    return rows, status, truncated, query_count


async def harvest(
    name: str,
    feed: Feed,
    net: Net,
    *,
    options: JsonApiHarvestOptions | None = None,
) -> Record:
    config = CONFIGS[name]
    list_config = config["list"]
    settings = getattr(feed, "config", None) or {}
    context = {
        "key": feed.key,
        "cursor": (list_config.get("page") or {}).get("start", 0),
        "appliedFacets": {},
    }
    key_parts = config.get("key_parts", ())
    context.update(zip(key_parts, feed.key.split("/", len(key_parts) - 1)))
    context.update(settings)
    try:
        response = await _request(
            list_config, feed, net, context, first=True, concurrency=list_config.get("concurrency", 1)
        )
    except NetError:
        if list_config.get("catch_first_error"):
            return Record(truncated=True)
        raise
    first_status, first_etag = response.status, etag(response)
    if response.not_modified:
        return Record(http_status=304, etag=first_etag)

    body = response.json()
    rows = _items(body, list_config)
    page = rows
    context["envelope"] = {
        key: resolved
        for key, value in config.get("envelope", {}).items()
        if (resolved := _value(value, body, context)) is not None
    }
    page_config = list_config.get("page")
    if page_config and options is not None:
        page_config = dict(page_config)
        if options.page_step is not None:
            page_config["step"] = options.page_step
        if options.page_concurrency is not None:
            page_config["concurrency"] = options.page_concurrency
    if page_config and page_config.get("total"):
        context["total"] = _value(page_config["total"], body, context)
    last_status = response.status
    partition_count = 0
    result_window_truncated = False
    pages = 1
    # A `page.max` cap bites here (parallel) or in the sequential loop below --
    # either way the rows already fetched are complete, real rows, so unlike
    # the partition/error paths they still deserve a detail fetch. Threaded
    # into every return below instead of returning early, so a capped list
    # never silently ships without descriptions (see `_finish`).
    pagination_truncated = False
    # A failed list page loses the rows after it, never the ones before it --
    # where the source can emit those rows honestly. A gated detail leg
    # (`only`, Workday) already emits the bare list row of every row its gate
    # declines, so the fetched rows go on through the gate and detail exactly
    # as on a complete listing, flagged `truncated` by `_finish`. Returning
    # nothing instead turned one exhausted page into an empty poll: Target,
    # CVS, RTX, Mastercard and Airbus all came back `partial` with 0 items on
    # 2026-10-05. An ungated detail source still returns nothing, because its
    # bare list rows lack what parse needs and would overwrite stored ones.
    keeps_partial_list = (config.get("detail") or {}).get("only") is not None
    list_failed = False
    concurrency = page_config.get("concurrency", 1) if page_config else 1
    partition = list_config.get("partition")
    count_probe = list_config.get("count_probe")
    observed_total = context.get("total")
    probe_now = (
        options.now
        if options is not None and options.now is not None
        else datetime.now(timezone.utc)
    )
    # Workday's public CXS list is a POST with `no-store`, so HTTP validators
    # cannot help. Its first page does carry `total`; use that only as a
    # time-bounded shortcut. Equal totals are not proof of equal contents, so a
    # successful full listing must be forced when the window expires. The
    # result-window cap is excluded because 2,000 is not the real board total.
    if (
        count_probe
        and not (options is not None and options.force_full)
        and isinstance(observed_total, int)
        and feed.list_total == observed_total
        and feed.last_full_poll_at is not None
        and (partition is None or observed_total < int(partition["window"]))
        and (probe_now - feed.last_full_poll_at).total_seconds()
        < int(count_probe["window_s"])
    ):
        return Record(
            envelope=context["envelope"],
            http_status=first_status,
            etag=first_etag,
            count_unchanged=True,
            list_total=observed_total,
            enumerated_item_count=len(rows),
        )
    if (
        page_config
        and partition
        and isinstance(context.get("total"), int)
        and context["total"] >= int(partition["window"])
    ):
        result_window_truncated = True
        rows, partition_status, list_truncated, partition_count = await _partition_capped_list(
            list_config,
            feed,
            net,
            context,
            page_config,
            body,
            partition,
            concurrency=concurrency,
        )
        if partition_status is not None:
            last_status = partition_status
        if list_truncated and keeps_partial_list:
            list_failed = True
        elif list_truncated:
            partial_items = _record_items(config, rows, context) if "detail" not in config else []
            return Record(
                items=partial_items,
                envelope=context["envelope"],
                truncated=True,
                http_status=first_status,
                etag=first_etag,
                enumerated_item_count=len(rows),
                partition_count=partition_count,
                result_window_truncated=True,
            )
        page = []
    elif (
        page_config and concurrency > 1
        and _parallel_eligible(page_config, context)
        and _more(page, rows, page_config, context)
    ):
        outcome, parallel_status, page = await _paginate_parallel(
            list_config, feed, net, context, page_config, rows, concurrency=concurrency
        )
        if parallel_status is not None:
            last_status = parallel_status
        if outcome == "error" and keeps_partial_list:
            list_failed = True
        elif outcome == "error":
            return Record(
                items=_record_items(config, rows, context) if "detail" not in config else [],
                envelope=context["envelope"], truncated=True, http_status=first_status, etag=first_etag,
                enumerated_item_count=len(rows),
            )
        if outcome == "capped":
            pagination_truncated = True
        page = []
    while page_config and _more(page, rows, page_config, context):
        if pages >= page_config.get("max", float("inf")):
            pagination_truncated = True
            break
        step = len(page) if page_config.get("step") == "count" else page_config.get("step", page_config.get("size", 1))
        context["cursor"] += step
        try:
            response = await _request(list_config, feed, net, context, first=False)
        except NetError:
            if keeps_partial_list:
                list_failed = True
                break
            return Record(
                items=_record_items(config, rows, context) if "detail" not in config else [],
                envelope=context["envelope"], truncated=True, http_status=first_status, etag=first_etag,
                enumerated_item_count=len(rows),
            )
        last_status = response.status
        body = response.json()
        page = _items(body, list_config)
        rows.extend(page)
        pages += 1

    detail = config.get("detail")
    if not detail:
        return Record(
            items=_record_items(config, rows, context),
            envelope=context["envelope"],
            truncated=pagination_truncated,
            http_status=last_status if config.get("last_status") else first_status,
            etag=first_etag,
            list_total=observed_total if isinstance(observed_total, int) else None,
            full_listing=bool(count_probe) and not pagination_truncated,
            enumerated_item_count=len(rows),
            partition_count=partition_count,
            result_window_truncated=result_window_truncated,
        )
    if options is not None and options.detail_concurrency is not None:
        detail = {**detail, "concurrency": options.detail_concurrency}

    items: list[Item] = []
    reused_item_count = 0
    detail_fetched_count = 0
    detail_skipped_count = 0
    skipped_rows: list[dict] = []
    seen: set[str] = set()
    pending: list[tuple[str, dict]] = []
    # A source may fetch detail for only the rows it actually wants. `only` is
    # an ordinary field spec evaluated against the list row, so it accepts every
    # form the config language already has -- `classify`, `eq`, a bare `@field`.
    # Absent (every source today), the loop below is unchanged.
    gate = detail.get("only")
    for row in rows:
        detail_id = _item_id(detail.get("id", list_config["id"]), row, context)
        dedupe = _item_id(detail.get("dedupe"), row, context) if detail.get("dedupe") else detail_id
        if detail_id is None or (dedupe in seen if dedupe else False):
            continue
        if dedupe:
            seen.add(dedupe)
        # Gated after dedupe so a skipped row still claims its identity: two
        # list rows sharing one detail page must not both count as skipped.
        if gate is not None and _value(gate, row, context) is not True:
            detail_skipped_count += 1
            skipped_rows.append(row)
            continue
        pending.append((detail_id, row))

    # `max` is to the detail leg what `page.max` is to the list: a ceiling on
    # what one poll will spend. A detail board pays one request per row, so
    # without it the only bound is the vendor's row count. Reports `truncated`
    # when it bites, exactly as the list cap does, so the shortfall is never
    # silent.
    detail_max = detail.get("max")
    detail_capped = detail_max is not None and len(pending) > detail_max
    if detail_capped:
        pending = pending[:detail_max]

    # A detail request that exhausts its retries costs us that row, not the
    # run: the fan-out has already issued and paid for every sibling request,
    # so returning early here discarded work already done -- one 429 on a
    # 781-row Workday board threw away 400 successfully fetched postings and
    # reported `partial`. Skip the row, flag `truncated`, keep the rest, as
    # avature.py and taleo.py already do on the same failure.
    detail_failed = False

    def _finish(*, truncated: bool = False) -> Record:
        truncated = (
            truncated or detail_capped or pagination_truncated or detail_failed or list_failed
        )
        return Record(
            # Gated rows carry their list payload, exactly as a source with no
            # detail leg emits every row. Appended rather than interleaved:
            # detail items keep list-row order among themselves, which the
            # concurrency path depends on.
            items=items + _record_items(config, skipped_rows, context),
            envelope=context["envelope"], truncated=truncated,
            http_status=last_status if config.get("last_status") else first_status, etag=first_etag,
            reused_item_count=reused_item_count,
            enumerated_item_count=len(rows),
            partition_count=partition_count,
            detail_fetched_count=detail_fetched_count,
            detail_skipped_count=detail_skipped_count,
            result_window_truncated=result_window_truncated,
            # A source with a detail leg never set this before -- only the
            # no-detail return branch above did. Without it, a vendor whose
            # list also configures `count_probe` (bamboohr) would persist
            # `list_total=NULL` on every full pass and the probe could never
            # find a stored baseline to compare against.
            list_total=observed_total if isinstance(observed_total, int) else None,
            # Two ways to earn a full listing, and both require `not truncated`.
            #
            # A forced all-detail pass is the reconciliation boundary for a
            # time-bounded reuse policy such as Gem's.
            #
            # A `count_probe` source earns one by enumerating the whole board,
            # exactly as the no-detail return above does. Without this clause a
            # vendor that configures both -- workday, since its detail leg came
            # back gated on 2026-08-26 -- would report `full_listing=False` on
            # every ordinary poll, so the probe would never store a baseline to
            # compare the next `total` against and the 24h reconciliation would
            # never fire.
            #
            # `not truncated` is load-bearing beyond tidiness here: a detail
            # request that exhausts its retries DROPS that row from `items`
            # (see the `continue` below), so a poll that claimed to be a full
            # listing while a detail leg failed would present a live posting as
            # absent. `truncated` covers detail_failed and detail_capped, which
            # is exactly the guard that needs.
            full_listing=(
                bool(count_probe) or bool(options is not None and options.force_full)
            ) and not truncated,
        )

    batch = detail.get("batch")
    if not batch:
        prior_items = (
            options.prior_items
            if options is not None and not options.force_full
            else None
        )
        revision_fields = options.revision_fields if options is not None else ()
        list_revision_fields = (
            (options.list_revision_fields or revision_fields) if options is not None else ()
        )
        detail_revision_fields = (
            (options.detail_revision_fields or revision_fields) if options is not None else ()
        )
        cached: dict[int, Item] = {}
        misses: list[tuple[int, str, dict]] = []
        merge = bool(detail.get("merge"))
        for index, (detail_id, row) in enumerate(pending):
            prior = prior_items.get(detail_id) if prior_items is not None else None
            row_revision = _revision(row, list_revision_fields)
            prior_revision = (
                _revision(prior.payload, detail_revision_fields)
                if prior is not None and isinstance(prior.payload, dict)
                # A merged payload is the list row plus the detail response's
                # keys; a prior with no key beyond the row never got its
                # detail (the gate declined it then), so it is not reusable.
                and (not merge or set(prior.payload) - set(row))
                else None
            )
            if row_revision is not None and row_revision == prior_revision:
                # Merged, only the detail half is reused: the live row's own
                # fields (Workday's `postedOn`, `remoteType`) are this poll's.
                cached[index] = (
                    Item(external_id=prior.external_id, payload={**prior.payload, **row})
                    if merge else prior
                )
            else:
                misses.append((index, detail_id, row))

        concurrency = detail.get("concurrency", 1)
        if concurrency > 1:
            # One request per pending row, bounded by Net's per-host
            # semaphore. Unlike the serial path, every request is issued even
            # if an early one fails -- same trade-off as _paginate_parallel.
            results = await asyncio.gather(
                *(
                    _request(detail, feed, net, {**context, "id": detail_id},
                             first=False, concurrency=concurrency)
                    for _, detail_id, _ in misses
                ),
                return_exceptions=True,
            )
            detail_fetched_count += len(misses)
            fetched = {index: result for (index, _, _), result in zip(misses, results)}
        else:
            fetched = None
        for index, (detail_id, row) in enumerate(pending):
            prior = cached.get(index)
            if prior is not None:
                items.append(prior)
                reused_item_count += 1
                continue
            detail_context = {**context, "id": detail_id}
            if fetched is None:
                try:
                    detail_response = await _request(detail, feed, net, detail_context, first=False)
                    detail_fetched_count += 1
                except NetError:
                    detail_failed = True
                    continue
            else:
                result = fetched[index]
                if isinstance(result, NetError):
                    detail_failed = True
                    continue
                if isinstance(result, BaseException):
                    raise result
                detail_response = result
            payload = _dig(detail_response.json(), detail.get("payload"))
            if not isinstance(payload, dict):
                detail_failed = True
                continue
            if detail.get("merge"):
                payload = {**row, **payload}
            external_id = _item_id(detail.get("item_id", list_config["id"]), payload, detail_context) or detail_id
            items.append(Item(external_id=external_id, payload=payload))
        return _finish()

    # Batched: one request covers `batch` rows, ids joined by "," into {id}.
    # The response's item order is not trusted -- each returned entry is
    # matched back to its row by `match`/`id`, not by position.
    match_spec = detail.get("match", detail.get("id", list_config["id"]))
    chunks = [pending[start : start + batch] for start in range(0, len(pending), batch)]
    chunk_contexts = [
        {**context, "id": ",".join(detail_id for detail_id, _ in chunk)} for chunk in chunks
    ]
    concurrency = detail.get("concurrency", 1)
    if concurrency > 1:
        # Chunks fetched together, bounded by Net's per-host semaphore.
        # Unlike the serial path, every chunk is requested even if an early
        # one fails -- same trade-off as the per-item fan-out above.
        results = await asyncio.gather(
            *(
                _request(detail, feed, net, chunk_context, first=False, concurrency=concurrency)
                for chunk_context in chunk_contexts
            ),
            return_exceptions=True,
        )
        detail_fetched_count += len(chunk_contexts)
    else:
        results = None
    for index, (chunk, detail_context) in enumerate(zip(chunks, chunk_contexts)):
        if results is None:
            try:
                detail_response = await _request(detail, feed, net, detail_context, first=False)
                detail_fetched_count += 1
            except NetError:
                detail_failed = True
                continue
        else:
            result = results[index]
            if isinstance(result, NetError):
                detail_failed = True
                continue
            if isinstance(result, BaseException):
                raise result
            detail_response = result
        returned = _dig(detail_response.json(), detail.get("payload"))
        by_id = (
            {_item_id(match_spec, entry, detail_context): entry for entry in returned if isinstance(entry, dict)}
            if isinstance(returned, list) else {}
        )
        for detail_id, row in chunk:
            payload = by_id.get(detail_id)
            if not isinstance(payload, dict):
                detail_failed = True
                continue
            if detail.get("merge"):
                payload = {**row, **payload}
            external_id = _item_id(detail.get("item_id", list_config["id"]), payload, detail_context) or detail_id
            items.append(Item(external_id=external_id, payload=payload))
    return _finish()


def parse(name: str, feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    config = CONFIGS[name]
    if isinstance(item.payload, dict) and "_probe" in item.payload:
        return _parse_probe(item, item.payload["_probe"])
    settings = getattr(feed, "config", None) or {}
    context = {"key": feed.key, "envelope": envelope}
    context.update(zip(config.get("key_parts", ()), feed.key.split("/", len(config.get("key_parts", ())) - 1)))
    context.update(settings)
    fields = {key: _value(value, item.payload, context) for key, value in config.get("fields", {}).items()}
    # `setdefault` fills a key the config never mapped; these fill a key it
    # mapped to a payload that turned out not to carry it. `fields` above comes
    # from a comprehension over every configured field, so such a field arrives
    # present-and-None and `setdefault` sees nothing to do -- all 27 configs map
    # `title`, which is why its default has never once fired despite backing a
    # NOT NULL column. Workday listings are where that surfaced: a requisition
    # pulled between the list walk and the detail fetch comes back as a bare
    # `externalPath` with no title, and the insert died on the constraint.
    for key, absent in (
        ("title", ""),
        ("url_posting", ""),
        ("fidelity", "origin"),
        ("state", "open"),
        ("published_at_precision", "unknown"),
        ("locations_extra", []),
        ("content_sections", []),
    ):
        if fields.get(key) is None:
            fields[key] = absent
    if fields.get("published_at") is None and fields.get("published_at_precision") in {"exact", "date"}:
        fields["published_at_precision"] = "unknown"
    fields["source_extra"] = {
        key: _value(value, item.payload, context) for key, value in config.get("extra", {}).items()
    }
    return [Posting(external_id=item.external_id, **fields)]


def _parse_probe(item: Item, evidence: dict) -> list[Canonical]:
    """Replay link/page closure evidence through the shared probe parser."""
    return parse_tombstone(item, evidence)


def harvester(name: str):
    return partial(harvest, name)


def parser(name: str):
    return partial(parse, name)


def get_adapter(name: str) -> dict:
    return CONFIGS[name]


get = get_adapter
