"""Shared config-driven adapter for in-house careers endpoints.

Companies: Amazon, Apple, Tesla, Uber (and extensible to others).

Each company config lives in inhouse_configs.py with:
  - endpoint, method (GET/POST)
  - pagination strategy (offset, page, or none)
  - items_path and total_path (dotted notation for nested JSON)
  - field mappings (dotted path, template, or lookup)

See sources/planned/inhouse.md for endpoint details and fixtures.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from xjobscore.net import Net, validators
from xjobscore.records import Canonical, Feed, Item, Posting, Record
from xjobscore.sources.inhouse_configs import CONFIGS


async def harvest(feed: Feed, net: Net) -> Record:
    """Fetch all items from endpoint, handling pagination and field extraction."""
    config = feed.config or CONFIGS.get(feed.key)
    if not config:
        raise ValueError(f"No config found for inhouse feed key={feed.key}")

    endpoint = config["endpoint"]
    method = config.get("method", "GET").upper()
    items_path = config.get("items_path")
    total_path = config.get("total_path")
    pagination_config = config.get("pagination", {})

    all_items = []
    http_status = None
    response_validators = {}
    envelope = {}
    truncated = False

    # Query parameters describe the provider endpoint; pagination only owns
    # the configured page/offset key. Keep them together so, for example,
    # Amazon's result_limit and Microsoft's locale/page size are not dropped.
    page_params = _build_page_params(config.get("query", {}), pagination_config)
    headers = config.get("headers")

    while True:
        # Build URL and request body
        if method == "GET":
            url = endpoint
            # Filter out internal tracking params
            params = {k: v for k, v in page_params.items() if not k.startswith("_")}
            body = None
            use_state = not all_items  # only use state for first request
        else:  # POST
            url = endpoint
            params = None
            # Interpolate page params into POST body template
            body = _interpolate_body(config.get("body", {}), page_params)
            use_state = not all_items

        # Make request
        try:
            if method == "GET":
                r = await net.get(
                    url,
                    params=params,
                    headers=headers,
                    state=feed.state if use_state else None,
                )
            else:
                r = await net.post(url, json=body, headers=headers)
        except Exception:
            truncated = True
            break

        http_status = r.status
        response_validators = validators(r)

        if r.not_modified:
            # Return early if not modified (only on first request)
            return Record(http_status=304, validators=response_validators)

        if r.status != 200:
            truncated = True
            break

        # Parse response
        try:
            response_body = r.json()
        except Exception:
            truncated = True
            break

        # Extract items via dotted path
        items_data = _extract_nested(response_body, items_path) if items_path else response_body
        if not isinstance(items_data, list):
            items_data = [items_data] if items_data else []

        # Extract total count for pagination logic
        total_count = None
        if total_path:
            total_count = _extract_nested(response_body, total_path)

        # Store envelope data (Tesla's lookup tables, etc.)
        if not envelope and "lookup" in response_body:
            envelope["lookup"] = response_body["lookup"]

        # Convert to Item objects
        for item_data in items_data:
            if not isinstance(item_data, dict):
                continue
            # Determine external_id from config
            id_field = config["fields"].get("external_id")
            id_value = _extract_nested(item_data, id_field) if id_field else len(all_items)
            if id_value is None:
                continue
            external_id = str(id_value)
            all_items.append(Item(external_id=external_id, payload=item_data))

        # Check pagination end conditions
        if len(items_data) == 0:
            break

        if total_count is not None and len(all_items) >= total_count:
            break

        max_pages = pagination_config.get("max_pages", 1)
        page_num = page_params.get("_page_num", 1)
        if page_num >= max_pages:
            break

        # Advance pagination
        page_params = _advance_page(page_params, pagination_config)

    return Record(
        items=all_items,
        envelope=envelope,
        truncated=truncated,
        http_status=http_status,
        validators=response_validators,
    )


def parse(feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
    """Extract Posting from one item using config field mappings."""
    config = feed.config or CONFIGS.get(feed.key)
    if not config:
        return []

    payload = item.payload
    if not isinstance(payload, dict):
        return []

    fields_config = config.get("fields", {})

    # Extract required fields first
    external_id = _render_field(payload, fields_config.get("external_id"), envelope)
    title = _render_field(payload, fields_config.get("title"), envelope)
    url_posting = _render_field(payload, fields_config.get("url_posting"), envelope)

    # If required fields missing, skip this item
    if not external_id or not title or not url_posting:
        return []

    # Extract optional fields
    company_raw = config.get("company_slug") or _render_field(
        payload, fields_config.get("company_raw"), envelope
    )
    location_raw = _render_field(payload, fields_config.get("location_raw"), envelope)
    description_html = _render_field(payload, fields_config.get("description_html"), envelope)
    employment_type = _render_field(payload, fields_config.get("employment_type"), envelope)
    workplace_type_raw = _render_field(payload, fields_config.get("workplace_type_raw"), envelope)
    apply_url = _render_field(payload, fields_config.get("apply_url"), envelope)
    requisition_id = _render_field(payload, fields_config.get("requisition_id"), envelope)

    # Parse datetime fields
    published_at_str = _render_field(payload, fields_config.get("published_at"), envelope)
    published_at = None
    published_at_precision = "unknown"
    if published_at_str:
        try:
            published_at = datetime.fromisoformat(published_at_str)
            published_at_precision = "exact"
        except (ValueError, TypeError):
            pass

    source_updated_at_str = _render_field(payload, fields_config.get("source_updated_at"), envelope)
    source_updated_at = None
    if source_updated_at_str:
        try:
            source_updated_at = datetime.fromisoformat(source_updated_at_str)
        except (ValueError, TypeError):
            pass

    posting = Posting(
        external_id=item.external_id,
        title=title,
        url_posting=url_posting,
        fidelity="origin",
        state="open",
        company_raw=company_raw,
        apply_url=apply_url,
        location_raw=location_raw,
        description_html=description_html,
        requisition_id=requisition_id,
        employment_type=employment_type,
        workplace_type_raw=workplace_type_raw,
        published_at=published_at,
        published_at_precision=published_at_precision,
        source_updated_at=source_updated_at,
        locations_extra=[],
        content_sections=[],
        source_extra={},
    )

    return [posting]


# Helper functions


def _build_page_params(query: dict, pagination_config: dict) -> dict:
    """Initialize pagination parameters."""
    params = dict(query)
    if not pagination_config:
        return params

    page_type = pagination_config.get("type")
    parameter = pagination_config.get("param")
    start = pagination_config.get("start", 0 if page_type == "offset" else 1)
    if page_type == "offset":
        params[parameter or "offset"] = start
        params["_page_num"] = 1
    elif page_type == "page":
        params[parameter or "page"] = start
        params["_page_num"] = 1
    else:
        params["_page_num"] = 1

    return params


def _advance_page(page_params: dict, pagination_config: dict) -> dict:
    """Advance to next page."""
    params = page_params.copy()
    page_type = pagination_config.get("type")
    parameter = pagination_config.get("param")
    size = pagination_config.get("size", 50)

    if page_type == "offset":
        key = parameter or "offset"
        params[key] = params.get(key, 0) + size
    elif page_type == "page":
        key = parameter or "page"
        params[key] = params.get(key, pagination_config.get("start", 1)) + 1

    params["_page_num"] = params.get("_page_num", 1) + 1
    return params


def _interpolate_body(body_template: dict, page_params: dict) -> dict:
    """Interpolate pagination params into POST body template."""
    import json

    # Convert to JSON string, interpolate, convert back
    body_str = json.dumps(body_template)
    for key, value in page_params.items():
        if key.startswith("_"):
            continue  # Skip internal keys like _page_num
        body_str = body_str.replace(f'"{{{key}}}"', json.dumps(value))
        body_str = body_str.replace(f"{{{key}}}", json.dumps(value))

    return json.loads(body_str)


def _extract_nested(obj: Any, path: str | None) -> Any:
    """Extract value from nested dict using dotted notation."""
    if path is None:
        return obj

    if isinstance(path, str):
        parts = re.sub(r"\[([^\]]+)\]", r".\1", path).split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if current is None:
                return None
        return current

    return None


def _render_field(payload: dict, spec: Any, envelope: dict) -> str | None:
    """Render a field from config spec.

    Supports:
    - str (dotted path): extract via _extract_nested
    - {"template": "..."}: format string with payload and envelope variables
    - {"lookup": "key", "key": "field"}: item[field] -> envelope["lookup"][key][value]
    - {"const": "value"}: return literal value
    """
    if spec is None:
        return None

    if isinstance(spec, str):
        # Dotted path
        return _extract_nested(payload, spec)

    if isinstance(spec, dict):
        if "template" in spec:
            template = spec["template"]

            def substitute(match: re.Match[str]) -> str:
                value = _extract_nested(payload, match.group(1))
                if value is None:
                    raise KeyError(match.group(1))
                return str(value)

            try:
                return re.sub(r"{([^{}]+)}", substitute, template)
            except KeyError:
                return None

        elif "lookup" in spec:
            # Lookup in envelope["lookup"][key][field_value]
            lookup_name = spec["lookup"]
            field_name = spec["key"]

            # Get the field value from payload
            field_value = _extract_nested(payload, field_name)
            if field_value is None:
                return None

            # Look up in envelope
            if "lookup" in envelope:
                lookup_table = envelope["lookup"].get(lookup_name, {})
                return lookup_table.get(str(field_value))

        elif "const" in spec:
            return spec["const"]

    return None
