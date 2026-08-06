"""Small serialization and identity helpers shared by the product layer."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import re
import unicodedata
import uuid
from typing import Any


IDENTITY_NAMESPACE = uuid.UUID("d25148f2-3c9e-43be-82a8-e184d6b737b6")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def public_id(kind: str, *parts: object) -> str:
    value = "\0".join((kind, *(str(part) for part in parts)))
    return str(uuid.uuid5(IDENTITY_NAMESPACE, value))


def new_id() -> str:
    return str(uuid.uuid4())


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def content_hash(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()

