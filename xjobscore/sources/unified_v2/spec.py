"""Source-neutral configuration types for the unified adapter engine.

There are deliberately no source specifications or registries in this package.
An :class:`AdapterSpec` describes transport and extraction mechanics while
``bind`` supplies any narrowly scoped semantic normalizers.  Source identity and
production routing remain outside the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

Scope = Literal["response", "item", "current", "envelope", "context"]
Method = Literal["GET", "POST"]
ResponseFormat = Literal["json", "html", "jsonld", "xml"]
CanonicalKind = Literal["posting", "target", "label", "fact"]
ItemKind = Literal["json", "html", "xml"]


@dataclass(frozen=True, slots=True)
class Ref:
    """Read a dotted path from one pipeline scope."""

    path: str = ""
    scope: Scope = "item"


@dataclass(frozen=True, slots=True)
class Fallback:
    """Return the first value that is neither ``None`` nor an empty string."""

    values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class Join:
    """Resolve values, discard missing ones, and join their string forms."""

    values: tuple[object, ...]
    separator: str = ", "


@dataclass(frozen=True, slots=True)
class Each:
    """Evaluate ``value`` once for every row selected by ``items``."""

    items: object
    value: object = field(default_factory=lambda: Ref(scope="current"))
    drop_none: bool = True


@dataclass(frozen=True, slots=True)
class Normalize:
    """Apply named field normalizers in order."""

    value: object
    using: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Get:
    """Read ``path`` from the result of another expression."""

    value: object
    path: str


@dataclass(frozen=True, slots=True)
class Equal:
    """Compare two resolved values for equality."""

    left: object
    right: object


@dataclass(frozen=True, slots=True)
class Matches:
    """Apply a regular expression to a resolved string value."""

    value: object
    pattern: str
    flags: int = 0


@dataclass(frozen=True, slots=True)
class AllOf:
    """True when every nested expression is truthy."""

    values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    """True when any nested expression is truthy."""

    values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class Not:
    """Negate the truthiness of a resolved expression."""

    value: object


@dataclass(frozen=True, slots=True)
class Truthy:
    """Return the truthiness of a resolved expression."""

    value: object


@dataclass(frozen=True, slots=True)
class IfElse:
    """Resolve one of two expressions from a condition."""

    condition: object
    when_true: object
    when_false: object = None


@dataclass(frozen=True, slots=True)
class FindAll:
    """Select every XML element matching an ElementTree path."""

    path: str
    scope: Scope = "item"


@dataclass(frozen=True, slots=True)
class HtmlLinks:
    """Extract unique HTML links whose href matches ``pattern``.

    Each result is ``{"id": <named group>, "url": <absolute URL>}``.
    """

    pattern: str
    id_group: str = "id"
    scope: Scope = "response"


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """One HTTP request at the start of a harvest pipeline."""

    method: Method
    url: str
    params: Mapping[str, object] | None = None
    json: object | None = None
    data: object | None = None
    headers: Mapping[str, object] | None = None
    conditional: bool = True
    url_override_key: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseSpec:
    """How a successful response becomes a structured value."""

    format: ResponseFormat = "json"
    schema_type: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionSpec:
    """How a structured response becomes raw Items and an envelope."""

    items: object
    external_id: object
    envelope: Mapping[str, object] = field(default_factory=dict)
    include: object | None = None
    dedupe: object | None = None
    kind: ItemKind | None = None


@dataclass(frozen=True, slots=True)
class PaginationSpec:
    """Integer-cursor pagination and its completion evidence."""

    start: int = 0
    step: int = 1
    total: object | None = None
    page_count: object | None = None
    page_size: int | None = None
    max_pages: int = 100


@dataclass(frozen=True, slots=True)
class DiscoverySpec:
    """One optional request that contributes values to pipeline context."""

    request: RequestSpec
    response: ResponseSpec
    items: object
    values: Mapping[str, object]
    include: object | None = None
    skip_if: object | None = None


@dataclass(frozen=True, slots=True)
class DetailSpec:
    """A request/extraction pair executed once per listed row."""

    request: RequestSpec
    response: ResponseSpec
    extraction: ExtractionSpec
    retain_list: bool = False


@dataclass(frozen=True, slots=True)
class CanonicalSpec:
    """Map one raw Item to one canonical output value."""

    kind: CanonicalKind
    fields: Mapping[str, object]
    include: object | None = None


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """The complete source-neutral data flow for one adapter."""

    request: RequestSpec
    response: ResponseSpec
    extraction: ExtractionSpec
    outputs: tuple[CanonicalSpec, ...]
    pagination: PaginationSpec | None = None
    discovery: DiscoverySpec | None = None
    detail: DetailSpec | None = None
    context: Mapping[str, object] = field(default_factory=dict)
