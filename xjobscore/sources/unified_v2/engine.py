"""Source-neutral request-to-canonical adapter pipeline.

The engine owns reusable transport mechanics: optional context discovery,
integer pagination, list/detail request graphs, structured-document decoding,
raw-item demultiplexing, and canonical field expressions.  It deliberately
contains no source names, endpoints, registries, or vendor-specific parsers.
Narrow semantic logic (for example, structuring one free-form header) is
supplied as a named normalizer when a spec is bound.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from xjobscore.net import Net, NetError, Response, etag
from xjobscore.records import Canonical, Fact, Feed, Item, Label, Posting, Record, Target
from xjobscore.sources.unified_v2.spec import (
    AllOf,
    AdapterSpec,
    AnyOf,
    CanonicalSpec,
    DetailSpec,
    Each,
    Equal,
    ExtractionSpec,
    Fallback,
    FindAll,
    Get,
    HtmlLinks,
    IfElse,
    Join,
    Matches,
    Normalize,
    Not,
    PaginationSpec,
    Ref,
    RequestSpec,
    ResponseSpec,
    Truthy,
)

Normalizer = Callable[[Any], Any]


class UnifiedAdapter:
    """Bind one explicit source-neutral spec to ``harvest`` and ``parse``."""

    def __init__(
        self,
        spec: AdapterSpec,
        *,
        normalizers: Mapping[str, Normalizer] | None = None,
    ) -> None:
        self._validate_request(spec.request)
        self._validate_response(spec.response)
        if spec.discovery is not None:
            self._validate_request(spec.discovery.request)
            self._validate_response(spec.discovery.response)
        if spec.detail is not None:
            self._validate_request(spec.detail.request)
            self._validate_response(spec.detail.response)
        if spec.pagination is not None:
            if spec.pagination.step < 1:
                raise ValueError("pagination step must be positive")
            if spec.pagination.page_size is not None and spec.pagination.page_size < 1:
                raise ValueError("pagination page_size must be positive")
            if spec.pagination.max_pages < 1:
                raise ValueError("pagination max_pages must be positive")
        unknown_outputs = {output.kind for output in spec.outputs} - _CANONICAL_TYPES.keys()
        if unknown_outputs:
            raise ValueError(f"unsupported canonical outputs: {sorted(unknown_outputs)}")

        self.spec = spec
        self._normalizers = {**DEFAULT_NORMALIZERS, **(normalizers or {})}

    @staticmethod
    def _validate_request(request: RequestSpec) -> None:
        if request.method not in {"GET", "POST"}:
            raise ValueError(f"unsupported request method: {request.method}")
        if request.json is not None and request.data is not None:
            raise ValueError("a request cannot define both json and data")
        if request.method == "GET" and (request.json is not None or request.data is not None):
            raise ValueError("a GET request cannot define json or data")
        if request.method == "POST" and request.params is not None:
            raise ValueError("POST params are not supported by the Net contract")

    @staticmethod
    def _validate_response(response: ResponseSpec) -> None:
        if response.format not in {"json", "html", "jsonld", "xml"}:
            raise ValueError(f"unsupported response format: {response.format}")
        if response.schema_type is not None and response.format != "jsonld":
            raise ValueError("schema_type is supported only for JSON-LD responses")

    async def harvest(self, feed: Feed, net: Net) -> Record:
        """Execute discovery, list pagination, and optional detail hydration."""

        context = self._context(feed)
        context = self._configured_context(context)
        if self.spec.discovery is not None:
            context = await self._discover(feed, net, context)

        listed: list[_Extracted] = []
        envelope: dict[str, Any] = {}
        seen: set[str] = set()
        first_response: Response | None = None
        truncated = False
        pagination = self.spec.pagination
        cursor = pagination.start if pagination is not None else None
        page_index = 0
        retrieved_rows = 0

        while True:
            page_context = {**context, "page_index": page_index}
            if cursor is not None:
                page_context["cursor"] = cursor
            request_evaluation = _Evaluation(context=page_context)
            try:
                response = await self._request(
                    self.spec.request,
                    feed,
                    net,
                    request_evaluation,
                    conditional=page_index == 0,
                )
            except NetError:
                if page_index == 0:
                    raise
                truncated = True
                break

            if first_response is None:
                first_response = response
                if response.not_modified:
                    return Record(http_status=304, etag=etag(response))
            elif response.not_modified:
                truncated = True
                break

            decoded = self._decode(response, self.spec.response)
            evaluation = _Evaluation(
                response=decoded,
                current=decoded,
                context=page_context,
                response_url=response.url,
            )
            for key, expression in self.spec.extraction.envelope.items():
                value = self._evaluate(expression, evaluation)
                if value is not None:
                    envelope.setdefault(key, value)

            page_rows, raw_count = self._extract(
                self.spec.extraction,
                evaluation,
                seen=seen,
                inferred_kind=self._kind(self.spec.response),
            )
            listed.extend(page_rows)
            retrieved_rows += raw_count
            page_index += 1

            if pagination is None:
                break
            has_more = self._has_more(
                pagination,
                evaluation,
                pages_done=page_index,
                retrieved_rows=retrieved_rows,
                raw_count=raw_count,
            )
            if not has_more:
                break
            if raw_count == 0:
                # A server-reported total/page count says rows remain, but the
                # next page is empty.  That is an incomplete snapshot, not a
                # reason to issue identical empty requests up to max_pages.
                truncated = True
                break
            if page_index >= pagination.max_pages:
                truncated = True
                break
            cursor = pagination.start + page_index * pagination.step

        if self.spec.detail is None:
            items = [entry.item for entry in listed]
        else:
            items, detail_truncated = await self._details(
                self.spec.detail,
                feed,
                net,
                context,
                listed,
                envelope,
            )
            truncated = truncated or detail_truncated

        return Record(
            items=items,
            envelope=envelope,
            truncated=truncated,
            http_status=first_response.status if first_response is not None else None,
            etag=etag(first_response) if first_response is not None else None,
        )

    def parse(self, feed: Feed, item: Item, envelope: dict) -> list[Canonical]:
        """Map one replayed raw Item into zero or more canonical values."""

        structured_item: Any = item.payload
        if item.kind == "xml" and isinstance(item.payload, str):
            structured_item = ET.fromstring(item.payload)
        context = self._configured_context(self._context(feed, external_id=item.external_id))
        evaluation = _Evaluation(
            item=structured_item,
            current=structured_item,
            envelope=envelope,
            context=context,
        )
        outputs: list[Canonical] = []
        for output in self.spec.outputs:
            if output.include is not None and not self._evaluate(output.include, evaluation):
                continue
            values = {
                key: self._evaluate(expression, evaluation)
                for key, expression in output.fields.items()
            }
            outputs.append(self._canonical(output, values, item))
        return outputs

    def _context(self, feed: Feed, *, external_id: str | None = None) -> dict[str, Any]:
        context = {**(feed.config or {}), "config": feed.config or {}, "key": feed.key}
        if external_id is not None:
            context["external_id"] = external_id
        return context

    def _configured_context(self, context: dict[str, Any]) -> dict[str, Any]:
        context = dict(context)
        for key, expression in self.spec.context.items():
            context[key] = self._evaluate(expression, _Evaluation(context=context))
        return context

    async def _discover(self, feed: Feed, net: Net, context: dict[str, Any]) -> dict[str, Any]:
        discovery = self.spec.discovery
        assert discovery is not None
        initial = _Evaluation(context=context)
        if discovery.skip_if is not None and self._evaluate(discovery.skip_if, initial):
            return context
        response = await self._request(discovery.request, feed, net, initial, conditional=False)
        if response.not_modified:
            raise ValueError("a discovery request cannot resolve context from a 304 response")
        decoded = self._decode(response, discovery.response)
        evaluation = _Evaluation(
            response=decoded,
            current=decoded,
            context=context,
            response_url=response.url,
        )
        for row in _as_rows(self._evaluate(discovery.items, evaluation)):
            row_evaluation = evaluation.with_current(row)
            if discovery.include is not None and not self._evaluate(discovery.include, row_evaluation):
                continue
            values = {
                key: self._evaluate(expression, row_evaluation)
                for key, expression in discovery.values.items()
            }
            return {**context, **values}
        raise ValueError("discovery response contained no matching context row")

    async def _details(
        self,
        detail: DetailSpec,
        feed: Feed,
        net: Net,
        context: dict[str, Any],
        listed: list[_Extracted],
        envelope: dict[str, Any],
    ) -> tuple[list[Item], bool]:
        items: list[Item] = []
        seen: set[str] = set()
        for entry in listed:
            detail_context = {
                **context,
                "external_id": entry.external_id,
                "list_item": entry.row,
            }
            if isinstance(entry.row, Mapping):
                for key, value in entry.row.items():
                    detail_context.setdefault(str(key), value)
            request_evaluation = _Evaluation(
                item=entry.row,
                current=entry.row,
                envelope=envelope,
                context=detail_context,
            )
            try:
                response = await self._request(
                    detail.request,
                    feed,
                    net,
                    request_evaluation,
                    conditional=False,
                )
            except NetError:
                return items, True
            if response.not_modified:
                return items, True
            decoded = self._decode(response, detail.response)
            evaluation = _Evaluation(
                response=decoded,
                item=entry.row,
                current=decoded,
                envelope=envelope,
                context=detail_context,
                response_url=response.url,
            )
            for key, expression in detail.extraction.envelope.items():
                value = self._evaluate(expression, evaluation)
                if value is not None:
                    envelope.setdefault(key, value)
            extracted, _ = self._extract(
                detail.extraction,
                evaluation,
                seen=seen,
                inferred_kind=self._kind(detail.response),
            )
            if detail.retain_list:
                if not isinstance(entry.row, Mapping):
                    raise ValueError("retain_list requires object-shaped list rows")
                for value in extracted:
                    if not isinstance(value.item.payload, dict):
                        raise ValueError("retain_list requires object-shaped detail rows")
                    items.append(
                        Item(
                            external_id=value.external_id,
                            payload={"list": dict(entry.row), "detail": value.item.payload},
                        )
                    )
            else:
                items.extend(value.item for value in extracted)
        return items, False

    def _extract(
        self,
        extraction: ExtractionSpec,
        evaluation: _Evaluation,
        *,
        seen: set[str],
        inferred_kind: str,
    ) -> tuple[list[_Extracted], int]:
        rows = _as_rows(self._evaluate(extraction.items, evaluation))
        extracted: list[_Extracted] = []
        for row in rows:
            row_evaluation = _Evaluation(
                response=evaluation.response,
                item=row,
                current=row,
                envelope=evaluation.envelope,
                context=evaluation.context,
                response_url=evaluation.response_url,
            )
            if extraction.include is not None and not self._evaluate(extraction.include, row_evaluation):
                continue
            external_id = self._evaluate(extraction.external_id, row_evaluation)
            if external_id is None:
                continue
            external_id = str(external_id)
            dedupe = (
                self._evaluate(extraction.dedupe, row_evaluation)
                if extraction.dedupe is not None
                else external_id
            )
            dedupe_key = str(dedupe) if dedupe is not None else None
            if dedupe_key is not None and dedupe_key in seen:
                continue
            if dedupe_key is not None:
                seen.add(dedupe_key)
            item = _item(row, external_id, extraction.kind or inferred_kind)
            if item is not None:
                extracted.append(_Extracted(row=row, external_id=external_id, item=item))
        return extracted, len(rows)

    def _has_more(
        self,
        pagination: PaginationSpec,
        evaluation: _Evaluation,
        *,
        pages_done: int,
        retrieved_rows: int,
        raw_count: int,
    ) -> bool:
        if pagination.page_count is not None:
            count = self._evaluate(pagination.page_count, evaluation)
            return count is not None and pages_done < int(count)
        if pagination.total is not None:
            total = self._evaluate(pagination.total, evaluation)
            return total is not None and retrieved_rows < int(total)
        if pagination.page_size is not None:
            return raw_count >= pagination.page_size
        return raw_count > 0

    def _evaluate(self, expression: object, evaluation: _Evaluation) -> Any:
        if isinstance(expression, Ref):
            return _dig(getattr(evaluation, expression.scope), expression.path)
        if isinstance(expression, Get):
            return _dig(self._evaluate(expression.value, evaluation), expression.path)
        if isinstance(expression, Fallback):
            for candidate in expression.values:
                value = self._evaluate(candidate, evaluation)
                if value not in (None, ""):
                    return value
            return None
        if isinstance(expression, Join):
            values = [self._evaluate(value, evaluation) for value in expression.values]
            return expression.separator.join(str(value) for value in values if value not in (None, "")) or None
        if isinstance(expression, Each):
            rows = self._evaluate(expression.items, evaluation)
            if not isinstance(rows, (list, tuple)):
                return []
            values = [self._evaluate(expression.value, evaluation.with_current(row)) for row in rows]
            return [value for value in values if value is not None] if expression.drop_none else values
        if isinstance(expression, Normalize):
            value = self._evaluate(expression.value, evaluation)
            for name in expression.using:
                try:
                    normalizer = self._normalizers[name]
                except KeyError as exc:
                    raise ValueError(f"unknown normalizer: {name}") from exc
                value = normalizer(value)
            return value
        if isinstance(expression, Equal):
            return self._evaluate(expression.left, evaluation) == self._evaluate(expression.right, evaluation)
        if isinstance(expression, Matches):
            value = self._evaluate(expression.value, evaluation)
            return isinstance(value, str) and re.search(expression.pattern, value, expression.flags) is not None
        if isinstance(expression, AllOf):
            return all(self._evaluate(value, evaluation) for value in expression.values)
        if isinstance(expression, AnyOf):
            return any(self._evaluate(value, evaluation) for value in expression.values)
        if isinstance(expression, Not):
            return not self._evaluate(expression.value, evaluation)
        if isinstance(expression, Truthy):
            return bool(self._evaluate(expression.value, evaluation))
        if isinstance(expression, IfElse):
            branch = expression.when_true if self._evaluate(expression.condition, evaluation) else expression.when_false
            return self._evaluate(branch, evaluation)
        if isinstance(expression, FindAll):
            base = getattr(evaluation, expression.scope)
            return base.findall(expression.path) if isinstance(base, ET.Element) else []
        if isinstance(expression, HtmlLinks):
            base = getattr(evaluation, expression.scope)
            if not isinstance(base, str):
                return []
            pattern = re.compile(self._render_text(expression.pattern, evaluation.context or {}))
            found: list[dict[str, str]] = []
            seen: set[str] = set()
            for href in _html(base).links:
                match = pattern.search(href)
                if match is None:
                    continue
                absolute = urljoin(evaluation.response_url or "", href)
                if absolute in seen:
                    continue
                seen.add(absolute)
                found.append({"id": match.group(expression.id_group), "url": absolute})
            return found
        if isinstance(expression, Mapping):
            return {key: self._evaluate(value, evaluation) for key, value in expression.items()}
        if isinstance(expression, list):
            return [self._evaluate(value, evaluation) for value in expression]
        if isinstance(expression, tuple):
            return tuple(self._evaluate(value, evaluation) for value in expression)
        return expression

    def _render_text(self, template: str, context: Mapping[str, Any]) -> str:
        def replace(match: re.Match[str]) -> str:
            token = match.group(1)
            value = _dig(context, token)
            if value is None:
                raise ValueError(f"unresolved template value: {token}")
            return str(value)

        return _TEMPLATE_TOKEN.sub(replace, template)

    async def _request(
        self,
        request: RequestSpec,
        feed: Feed,
        net: Net,
        evaluation: _Evaluation,
        *,
        conditional: bool,
    ) -> Response:
        context = evaluation.context or {}
        template = request.url
        if request.url_override_key is not None:
            template = feed.config.get(request.url_override_key, template)
        url = self._render_text(template, context)
        headers = self._evaluate(request.headers, evaluation) if request.headers is not None else None

        if request.method == "POST":
            json_body = self._evaluate(request.json, evaluation) if request.json is not None else None
            data = self._evaluate(request.data, evaluation) if request.data is not None else None
            return await net.post(url, json=json_body, data=data, headers=headers)

        kwargs: dict[str, Any] = {}
        if request.params is not None:
            kwargs["params"] = self._evaluate(request.params, evaluation)
        if headers is not None:
            kwargs["headers"] = headers
        if request.conditional and conditional:
            kwargs["etag"] = feed.etag
        return await net.get(url, **kwargs)

    @staticmethod
    def _decode(response: Response, spec: ResponseSpec) -> Any:
        if spec.format == "json":
            return response.json()
        if spec.format == "html":
            return response.text
        if spec.format == "xml":
            return ET.fromstring(response.text)
        if spec.format == "jsonld":
            return _jsonld(response.text, schema_type=spec.schema_type)
        raise ValueError(f"unsupported response format: {spec.format}")

    @staticmethod
    def _kind(spec: ResponseSpec) -> str:
        if spec.format == "xml":
            return "xml"
        if spec.format == "html":
            return "html"
        return "json"

    def _canonical(self, output: CanonicalSpec, values: dict[str, Any], item: Item) -> Canonical:
        canonical_type = _CANONICAL_TYPES[output.kind]
        field_names = {field.name for field in dataclass_fields(canonical_type)}
        if "external_id" in field_names:
            values.setdefault("external_id", item.external_id)
        if output.kind == "posting":
            values.setdefault("title", "")
            values.setdefault("url_posting", "")
            precision = values.get("published_at_precision", "unknown")
            if values.get("published_at") is None and precision in {"exact", "date"}:
                values["published_at_precision"] = "unknown"
        return canonical_type(**values)


def bind(
    spec: AdapterSpec,
    *,
    normalizers: Mapping[str, Normalizer] | None = None,
) -> UnifiedAdapter:
    """Create an isolated adapter without registering a source."""

    return UnifiedAdapter(spec, normalizers=normalizers)


_TEMPLATE_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_CANONICAL_TYPES = {
    "posting": Posting,
    "target": Target,
    "label": Label,
    "fact": Fact,
}


@dataclass(frozen=True, slots=True)
class _Evaluation:
    response: Any = None
    item: Any = None
    current: Any = None
    envelope: Mapping[str, Any] | None = None
    context: Mapping[str, Any] | None = None
    response_url: str | None = None

    def with_current(self, current: Any) -> _Evaluation:
        return _Evaluation(
            response=self.response,
            item=self.item,
            current=current,
            envelope=self.envelope,
            context=self.context,
            response_url=self.response_url,
        )


@dataclass(frozen=True, slots=True)
class _Extracted:
    row: Any
    external_id: str
    item: Item


class _HTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.links: list[str] = []
        self.scripts: list[str] = []
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if tag.lower() == "script" and (values.get("type") or "").lower() == "application/ld+json":
            self._script = []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


def _html(text: str) -> _HTML:
    parser = _HTML()
    parser.feed(text)
    parser.close()
    return parser


def _jsonld(text: str, *, schema_type: str | None) -> list[dict]:
    values: list[dict] = []

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for row in value:
                collect(row)
            return
        if not isinstance(value, dict):
            return
        graph = value.get("@graph")
        if isinstance(graph, list):
            for row in graph:
                collect(row)
            return
        kind = value.get("@type")
        matches = schema_type is None or kind == schema_type or (
            isinstance(kind, list) and schema_type in kind
        )
        if matches:
            values.append(value)

    for script in _html(text).scripts:
        try:
            collect(json.loads(script))
        except json.JSONDecodeError:
            continue
    return values


def _as_rows(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (Mapping, ET.Element)):
        return [value]
    return []


def _item(row: Any, external_id: str, kind: str) -> Item | None:
    if isinstance(row, ET.Element):
        return Item(external_id=external_id, payload=ET.tostring(row, encoding="unicode"), kind="xml")
    if isinstance(row, Mapping):
        return Item(external_id=external_id, payload=dict(row), kind="json")
    if isinstance(row, str) and kind in {"xml", "html"}:
        return Item(external_id=external_id, payload=row, kind=kind)
    return None


def _dig(value: Any, path: str) -> Any:
    if path in ("", "."):
        return _xml_text(value) if isinstance(value, ET.Element) else value
    if isinstance(value, ET.Element):
        return _dig_xml(value, path)
    for part in path.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(value, Mapping):
            value = value.get(part)
        elif (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            and part.isdigit()
            and int(part) < len(value)
        ):
            value = value[int(part)]
        else:
            return None
        if value is None:
            return None
    return value


def _dig_xml(element: ET.Element, path: str) -> Any:
    if "/@" in path:
        node_path, attribute = path.rsplit("/@", 1)
        node = element if node_path in ("", ".") else element.find(node_path)
        return node.get(attribute) if node is not None else None
    if path.startswith("@"):
        return element.get(path[1:])
    node = element.find(path)
    return _xml_text(node) if node is not None else None


def _xml_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def _html_unescape(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("html_unescape expects a string or None")
    return html.unescape(value)


def _iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("iso_datetime expects a string, datetime, or None")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rfc2822_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError("rfc2822_datetime expects a string, datetime, or None")
    return parsedate_to_datetime(value)


def _epoch_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _epoch_s(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _string(value: Any) -> str | None:
    return None if value is None else str(value)


DEFAULT_NORMALIZERS: Mapping[str, Normalizer] = {
    "html_unescape": _html_unescape,
    "iso_datetime": _iso_datetime,
    "rfc2822_datetime": _rfc2822_datetime,
    "epoch_ms": _epoch_ms,
    "epoch_s": _epoch_s,
    "string": _string,
}
