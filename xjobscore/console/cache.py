"""Console result snapshot model and display helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    result_id: str
    feed_id: int
    source: str
    key: str
    started_at: datetime
    finished_at: datetime
    elapsed_ms: float
    status: str
    http_status: int | None
    item_count: int
    raw_size_bytes: int
    truncated: bool
    error_class: str | None
    error_detail: str | None
    items: tuple[dict[str, Any], ...]
    envelope: Any
    size_bytes: int
    network: dict[str, Any] = field(default_factory=dict)
    trigger: str = "manual"  # 'manual' | 'scheduled'
    # Parse previews are derived on demand when a parsed pane is viewed
    # (service._with_parse); fetch and scheduled runs never populate these.
    parse_status: str | None = None  # None (not derived) | 'ok' | 'no_parser' | 'skipped'
    parse_summary: dict[str, Any] | None = None
    parse_items: tuple[dict[str, Any], ...] = ()
    # The scheduler's conditional-request validator. Deliberately excluded
    # from summary/API payloads, matching network.py's validator masking.
    response_etag: str | None = None

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            # Console responses may be consumed by a browser. Preserve this
            # internal 63-bit integer exactly rather than serializing it as a
            # JavaScript number.
            "feed_id": str(self.feed_id),
            "source": self.source,
            "key": self.key,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
            "status": self.status,
            "http_status": self.http_status,
            "item_count": self.item_count,
            "raw_size_bytes": self.raw_size_bytes,
            "truncated": self.truncated,
            "error_class": self.error_class,
            "error_detail": self.error_detail,
            "size_bytes": self.size_bytes,
            "network": self.network.get("summary", {}),
            "trigger": self.trigger,
            "parse": {"status": self.parse_status, "summary": self.parse_summary},
        }

    def item_summary(self, index: int) -> dict[str, Any]:
        item = self.items[index]
        return {
            "index": index,
            "external_id": item["external_id"],
            "kind": item["kind"],
            "display_name": _display_name(item),
            "display_context": _display_context(item),
            "raw_document": item["raw_document"],
        }

    def item_detail(self, index: int) -> dict[str, Any]:
        item = self.items[index]
        raw_document = dict(item["raw_document"])
        raw_document["payload_json"] = item["payload"] if item["kind"] == "json" else None
        raw_document["payload_text"] = item["payload"] if item["kind"] != "json" else None
        raw_document["envelope"] = self.envelope
        return {**self.item_summary(index), "payload": item["payload"], "raw_document": raw_document}

    def envelope_detail(self) -> dict[str, Any]:
        return {"result": self.summary, "envelope": self.envelope}


def estimate_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def estimate_raw_size(payload: Any, kind: str) -> int:
    """Bytes represented by one payload in its raw-document column."""
    if kind == "json":
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), default=str
            ).encode("utf-8")
        except (TypeError, ValueError):
            return 0
        return len(encoded)
    return len(payload.encode("utf-8")) if isinstance(payload, str) else 0


def _path(value: Any, *parts: str) -> Any:
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _xml_job_title(payload: str) -> str | None:
    """Extract an RSS/XML job title for the compact item picker only."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None
    preferred_tags = {"title", "jobtitle", "positiontitle", "jobopeningname", "name"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in preferred_tags and (title := _text(element.text)):
            return title
    return None


def _display_name(item: dict[str, Any]) -> str:
    """Best-effort job title for the picker, without changing raw payloads."""
    payload = item["payload"]
    if isinstance(payload, dict):
        for path in (
            # The current ATS adapters use these field names for a posting title.
            ("title",), ("jobTitle",), ("job_title",), ("positionTitle",),
            ("postingTitle",), ("posting_title",),
            ("position_title",), ("jobOpeningName",), ("Title",),
            ("text",), ("position",), ("name",),
            # Detail responses and JSON-LD commonly wrap the posting first.
            ("job", "title"), ("job", "name"), ("jobPosting", "title"),
            ("jobPosting", "name"), ("position", "title"),
        ):
            if name := _text(_path(payload, *path)):
                return name
    if isinstance(payload, str) and (title := _xml_job_title(payload)):
        return title
    # IDs and hashes belong in the detail view, not in the job picker.
    return "Untitled job"


def _display_context(item: dict[str, Any]) -> str | None:
    payload = item["payload"]
    if not isinstance(payload, dict):
        return None
    for path in (
        ("location", "name"), ("location",), ("categories", "location"),
        ("city",), ("locations", "0", "name"), ("categories", "allLocations", "0"),
    ):
        if context := _text(_path(payload, *path)):
            return context
    return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
