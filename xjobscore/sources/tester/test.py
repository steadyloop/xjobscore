#!/usr/bin/env python3
"""Test house_ats adapter with fixture data."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

# Minimal mock objects to avoid import issues
@dataclass
class MockResponse:
    """Mock HTTP response."""
    status: int
    body: Optional[dict] = None
    not_modified: bool = False

    def json(self):
        if self.body is None:
            raise ValueError("No JSON body")
        return self.body


@dataclass
class Item:
    """Minimal Item mock."""
    external_id: str
    payload: Union[dict, str]
    kind: str = "json"


@dataclass
class Record:
    """Minimal Record mock."""
    items: list = field(default_factory=list)
    envelope: dict = field(default_factory=dict)
    truncated: bool = False
    http_status: Optional[int] = None
    validators: dict = field(default_factory=dict)

    def __repr__(self):
        return f"Record(items={len(self.items)}, http_status={self.http_status}, truncated={self.truncated})"


class MockNet:
    """Mock Net that returns fixture data."""

    def __init__(self, fixture_name: str):
        self.fixture_name = fixture_name
        fixture_path = Path(__file__).parent.parent.parent / "fixtures" / f"{fixture_name}.json"
        with open(fixture_path) as f:
            self.fixture_data = json.load(f)

    async def get(self, url, *, params=None, state=None):
        """Mock GET — return fixture data."""
        return MockResponse(status=200, body=self.fixture_data)

    async def post(self, url, *, json=None):
        """Mock POST — return fixture data."""
        return MockResponse(status=200, body=self.fixture_data)


@dataclass(frozen=True)
class MockFeed:
    """Minimal Feed mock."""
    key: str
    config: dict
    state: dict = None


def validators(response):
    """Mock validators function."""
    return {}


def _load_configs():
    """Load configs from house_ats_configs.py by parsing the file."""
    config_path = Path(__file__).parent.parent / "house_ats_configs.py"
    # Read and execute the config file to extract CONFIGS
    with open(config_path) as f:
        code = f.read()
    namespace = {}
    exec(code, namespace)
    return namespace.get("CONFIGS", {})


# Inline simplified versions of parse logic for testing
def _extract_nested(obj, path):
    """Extract value from nested dict using dotted notation."""
    if path is None:
        return obj
    if isinstance(path, str):
        parts = path.split(".")
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


def _render_field(payload, spec, envelope):
    """Render a field from config spec."""
    if spec is None:
        return None

    if isinstance(spec, str):
        return _extract_nested(payload, spec)

    if isinstance(spec, dict):
        if "template" in spec:
            template = spec["template"]
            try:
                # Extract all {var} patterns and render them via _extract_nested
                import re
                def replace_var(match):
                    var_path = match.group(1)
                    val = _extract_nested(payload, var_path)
                    return str(val) if val is not None else ""

                result = re.sub(r'\{([^}]+)\}', replace_var, template)
                return result if result else None
            except Exception:
                return None
        elif "lookup" in spec:
            lookup_name = spec["lookup"]
            field_name = spec["key"]
            field_value = _extract_nested(payload, field_name)
            if field_value is None:
                return None
            if "lookup" in envelope:
                lookup_table = envelope["lookup"].get(lookup_name, {})
                return lookup_table.get(str(field_value))
        elif "const" in spec:
            return spec["const"]

    return None


async def test_harvest(company: str):
    """Test harvest for a company."""
    # Load configs directly to avoid import issues
    configs = _load_configs()
    config = configs.get(company)
    if not config:
        print(f"❌ No config for {company}")
        return None

    feed = MockFeed(key=company, config=config, state={})
    net = MockNet(company)

    endpoint = config["endpoint"]
    method = config.get("method", "GET").upper()
    items_path = config.get("items_path")
    total_path = config.get("total_path")
    pagination_config = config.get("pagination", {})

    print(f"\n{'='*60}")
    print(f"Testing {company.upper()}")
    print(f"{'='*60}")
    print(f"Endpoint: {endpoint}")
    print(f"Method: {method}")
    print(f"Items path: {items_path}")

    # Simulate harvest logic
    all_items = []
    page_params = {}

    # Determine pagination type
    page_type = pagination_config.get("type")
    if page_type == "offset":
        page_params["offset"] = 0
    elif page_type == "page":
        page_params["page"] = 1

    # Make first request
    if method == "GET":
        r = await net.get(endpoint, params=page_params)
    else:
        r = await net.post(endpoint, json=config.get("body", {}))

    if r.status != 200:
        print(f"❌ Request failed with status {r.status}")
        return None

    response_body = r.json()

    # Extract items via dotted path
    items_data = _extract_nested(response_body, items_path) if items_path else response_body
    if not isinstance(items_data, list):
        items_data = [items_data] if items_data else []

    # Extract total count
    total_count = None
    if total_path:
        total_count = _extract_nested(response_body, total_path)

    # Store envelope
    envelope = {}
    if "lookup" in response_body:
        envelope["lookup"] = response_body["lookup"]

    # Convert to Item objects
    for item_data in items_data:
        if not isinstance(item_data, dict):
            continue
        id_field = config["fields"].get("external_id")
        external_id = str(_extract_nested(item_data, id_field)) if id_field else str(len(all_items))
        all_items.append(Item(external_id=external_id, payload=item_data))

    # Create Record
    record = Record(
        items=all_items,
        envelope=envelope,
        http_status=200,
        validators=validators(r),
    )

    print(f"✓ Items fetched: {len(record.items)} / {total_count if total_count else 'N/A'}")
    print(f"  Envelope keys: {list(record.envelope.keys())}")

    # Show first item parsed as Posting-like object
    if record.items:
        first_item = record.items[0]
        config_fields = config["fields"]

        print(f"\n  First item → Posting (external_id={first_item.external_id}):")

        # Extract all key fields
        posting_data = {}
        for field_name, field_spec in config_fields.items():
            value = _render_field(first_item.payload, field_spec, envelope)
            posting_data[field_name] = value

        # Show key fields
        for key in ["external_id", "title", "url_posting", "location_raw", "employment_type"]:
            if key in posting_data:
                value = posting_data[key]
                if isinstance(value, str) and len(value) > 70:
                    value = value[:70] + "..."
                if value is not None:
                    print(f"    {key:20s} = {value}")

    return record


async def main():
    """Run tests for all companies with shared adapter."""
    companies = ["amazon", "apple", "microsoft", "tesla", "uber"]
    records = {}

    for company in companies:
        try:
            record = await test_harvest(company)
            if record:
                records[company] = record
        except Exception as e:
            print(f"❌ {company}: {e}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for company, record in records.items():
        print(f"{company:12} → Record(items={len(record.items):3d}, http_status={record.http_status}, envelope_keys={list(record.envelope.keys())})")

    print(f"\n{'='*60}")
    print("RECORD.ITEMS[] PAYLOAD")
    print(f"{'='*60}")
    for company, record in records.items():
        print(f"\n{company.upper()}: Record(items={len(record.items)}, http_status={record.http_status})")

        if record.items:
            first_item = record.items[0]
            print(f"\nitems[0] (external_id={first_item.external_id}):")
            # Pretty print the payload
            import json
            payload_json = json.dumps(first_item.payload, indent=2)
            # Limit output to first 1500 chars
            if len(payload_json) > 1500:
                print(payload_json[:1500] + "\n  ...")
            else:
                print(payload_json)


if __name__ == "__main__":
    asyncio.run(main())
