"""Match job-description text against degree fields in ``registry/job-posting/types.yaml``."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["DEGREE_PATH", "DegreeError", "DegreeField", "canon", "extract"]

DEGREE_PATH = Path(__file__).parent / "registry" / "job-posting" / "types.yaml"
_FIELDS = {"name", "patterns"}
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


class DegreeError(ValueError):
    """The degree section of the type canon is malformed."""


@dataclass(frozen=True, slots=True)
class DegreeField:
    name: str
    patterns: tuple[re.Pattern[str], ...]


def _entry(row: object, index: int, path: Path) -> DegreeField:
    where = f"{path}: degree[{index}]"
    if not isinstance(row, dict):
        raise DegreeError(f"{where} must be a mapping")
    unknown = sorted(set(row) - _FIELDS)
    if unknown:
        raise DegreeError(f"{where}: unknown key {unknown[0]!r}")

    name = row.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise DegreeError(f"{where}: name must be a lowercase snake_case identifier")

    source_patterns = row.get("patterns")
    if not isinstance(source_patterns, list) or not source_patterns:
        raise DegreeError(f"{where}: patterns must be a non-empty array")
    patterns = []
    for pattern in source_patterns:
        if not isinstance(pattern, str) or not pattern:
            raise DegreeError(f"{where}: patterns must contain non-empty strings")
        try:
            patterns.append(re.compile(pattern))
        except re.error as exc:
            raise DegreeError(f"{where}: invalid pattern {pattern!r}: {exc}") from exc
    return DegreeField(name=name, patterns=tuple(patterns))


def _load(path: Path) -> tuple[DegreeField, ...]:
    try:
        with path.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise DegreeError(f"{path}: {exc}") from exc

    if not isinstance(document, dict) or "degree" not in document:
        raise DegreeError(f"{path}: root must contain a 'degree' array")
    unknown = sorted(set(document) - {"type", "degree"})
    if unknown:
        raise DegreeError(f"{path}: unknown root key {unknown[0]!r}")
    rows = document["degree"]
    if not isinstance(rows, list):
        raise DegreeError(f"{path}: degree must be an array")

    fields = tuple(_entry(row, index, path) for index, row in enumerate(rows))
    if not fields:
        raise DegreeError(f"{path}: degree canon is empty")
    names = [field.name for field in fields]
    if len(names) != len(set(names)):
        raise DegreeError(f"{path}: duplicate degree field name")
    return fields


@functools.lru_cache(maxsize=1)
def canon() -> tuple[DegreeField, ...]:
    """Return validated degree fields, loading the shared YAML canon once."""
    return _load(DEGREE_PATH)


def extract(text: str) -> list[str]:
    """Return sorted degree fields explicitly mentioned in ``text``."""
    return sorted(
        field.name
        for field in canon()
        if any(pattern.search(text or "") for pattern in field.patterns)
    )
