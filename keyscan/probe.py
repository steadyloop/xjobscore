"""Standalone probe engine, shared by the crawl and by callers that only need
to classify a single token's liveness (e.g. the inspector's Tokens page).

The logic here was factored out of ``Keyscan._probe`` so both the full crawl
and one-off validators run *exactly* the same per-vendor rules. Everything is
free functions that take their I/O dependency as a ``request`` callable, so the
crawl can hand in its throttled/host-locked ``_request`` while a lightweight
caller hands in :func:`default_request` over a plain ``httpx.AsyncClient``.

Returned status is three-valued -- ``live`` / ``absent`` / ``inconclusive`` --
where ``inconclusive`` means "no evidence either way, do not act". Callers that
persist a two-and-a-half-valued column map it themselves.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from .rules import Rule
# Pure helpers stay defined in core; import them here to keep one source of
# truth. core imports this module lazily (inside Keyscan._probe) to avoid a
# circular import at load time.
from .core import (
    COMEET_API_TOKEN,
    _company_name,
    _count_at_path,
    _html_company_name,
    _html_fingerprint,
    _job_count,
    _xml_job_count,
)

# request(method, url, *, json_body=None, headers=None) -> httpx.Response | None
RequestFn = Callable[..., Awaitable["httpx.Response | None"]]

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


async def default_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response | None:
    """A minimal :data:`RequestFn` over an existing client.

    Retries transient failures up to three times. Unlike the crawl's
    ``_request`` it does not throttle per host or emit timing logs -- those
    matter only when saturating a crawl, not for a handful of validations.
    """
    response: httpx.Response | None = None
    for attempt in range(3):
        try:
            response = await client.request(method, url, json=json_body, headers=headers)
        except (httpx.TimeoutException, httpx.HTTPError):
            response = None
        else:
            if response.status_code not in _RETRY_STATUS:
                return response
        if attempt < 2:
            # backoff is left to the client's own timeouts; keep it simple.
            continue
    return response


async def probe_rule(
    rule: Rule,
    token: str,
    *,
    request: RequestFn,
    controls: dict[str, str | None] | None = None,
    canonical_url: str = "",
    liveness_only: bool = False,
) -> tuple[str, int | None, dict[str, Any]]:
    """Probe one ``(rule, token)`` and return ``(status, count, details)``.

    ``status`` is ``live`` / ``absent`` / ``inconclusive``. ``controls`` is a
    per-vendor cache of generic-page fingerprints; pass a dict to reuse it
    across calls (the crawl does), or leave it ``None`` for a fresh one.

    ``liveness_only`` narrows the question to "does this board exist" and lets
    a rule swap in its cheap variant (``light_url`` / ``light_method``). The
    crawl leaves it off because it persists ``job_count``; the inspector's
    Tokens page turns it on because it stores nothing but ``token.status``.
    """
    if controls is None:
        controls = {}
    if rule.probe_kind == "comeet":
        return await _probe_comeet(rule, token, request=request, canonical_url=canonical_url)
    fields = rule.urls(token)
    if rule.exists_url:
        exists, exists_responses = await _probe_exists(rule, fields, request=request)
        if exists is False:
            return "absent", None, {"responses": exists_responses, "reason": "board_not_registered"}
        if exists is None:
            # Could not tell. Falling through to the job endpoint would be
            # worse than saying so: for the vendors that need this pre-check,
            # that endpoint answers 200 for tokens that do not exist.
            return "inconclusive", None, {"responses": exists_responses, "reason": "exists_check_failed"}
    primary = rule.probe_url if rule.probe_kind != "html" else rule.board_url
    fallbacks = rule.fallbacks
    method = rule.method
    if liveness_only:
        # Only the request changes; every verdict below is reached by the same
        # ladder, so a light rule cannot answer differently from a heavy one
        # except by skipping the body checks it no longer has a body for.
        primary = rule.light_url or primary
        fallbacks = rule.light_fallbacks or (() if rule.light_url else fallbacks)
        method = rule.light_method or method
    responses: list[tuple[str, int | None]] = []
    response: httpx.Response | None = None
    for template in (primary, *fallbacks):
        endpoint = template.format(**fields)
        response = await request(method, endpoint, json_body=rule.body, headers=rule.headers)
        status = response.status_code if response is not None else None
        responses.append((endpoint, status))
        if status not in {404, 410}:
            break
    if response is None:
        return "inconclusive", None, {"responses": responses, "reason": "transport"}
    if all(status in {404, 410} for _, status in responses):
        return "absent", None, {"responses": responses}
    if response.status_code != 200:
        return "inconclusive", None, {"responses": responses, "reason": f"http_{response.status_code}"}
    if method == "HEAD":
        # There is no body to read, and that is the point: a rule only opts
        # into HEAD when a nonexistent board 404s, so the 200 already settles
        # it. Count is None, the same as the html path returns. A live board
        # with zero open jobs reports `live` here rather than falling to the
        # `zero_count` guard below -- correct for the narrower question.
        return "live", None, {"responses": responses, "method": "HEAD"}
    if rule.probe_kind == "json":
        try:
            payload = response.json()
        except ValueError:
            return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
        count = _count_at_path(payload, rule.count_path) if rule.count_path else _job_count(payload)
        if count is None:
            return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
        details: dict[str, Any] = {"responses": responses}
        company_name = _company_name(payload)
        if company_name:
            details.update(company_name=company_name, company_name_source="probe_json")
        elif rule.name_url:
            name, name_responses = await _probe_name(rule, fields, request=request)
            details["name_responses"] = name_responses
            if name:
                details.update(company_name=name, company_name_source="metadata_json")
                company_name = name
        if count == 0 and not company_name:
            # A zero total is ambiguous: some APIs (SmartRecruiters'
            # postings search, at least) return this identical shape for
            # a nonexistent company id and for a real one with no open
            # postings right now, with no distinguishing metadata either
            # way. Without a company name to confirm the id resolved to
            # something real, don't count a bare zero as evidence of life.
            details["reason"] = "zero_count"
            return "inconclusive", None, details
        return "live", count, details
    if rule.probe_kind == "xml":
        if any(marker not in response.text for marker in rule.xml_required_markers):
            return "inconclusive", None, {"responses": responses, "reason": "missing_xml_marker"}
        count = _xml_job_count(response.text)
        if count is None:
            return "inconclusive", None, {"responses": responses, "reason": "unrecognized_xml"}
        return "live", count, {"responses": responses}
    full_body = response.text.lower()
    body = full_body[:20000]
    if any(marker in body for marker in rule.absent_markers):
        return "absent", None, {"responses": responses, "reason": "absent_marker"}
    if any(marker not in full_body for marker in rule.html_required_markers):
        return "inconclusive", None, {"responses": responses, "reason": "missing_html_marker"}
    if rule.html_any_markers and not any(marker in full_body for marker in rule.html_any_markers):
        return "inconclusive", None, {"responses": responses, "reason": "missing_jobboard_marker"}
    control = await _html_control(rule, fields, request=request, controls=controls)
    fingerprint = _html_fingerprint(body, token)
    if control and control == fingerprint:
        return "inconclusive", None, {"responses": responses, "reason": "generic_html"}
    if "<html" not in body and "<!doctype" not in body:
        return "inconclusive", None, {"responses": responses, "reason": "not_html"}
    details = {"responses": responses, "html_fingerprint": fingerprint}
    company_name = _html_company_name(response.text)
    if company_name:
        details.update(company_name=company_name, company_name_source="probe_html")
    return "live", None, details


async def _probe_exists(
    rule: Rule, fields: dict[str, str], *, request: RequestFn,
) -> tuple[bool | None, list[tuple[str, int | None]]]:
    """Ask a board-scoped endpoint whether this token names a real board.

    Returns ``True`` (registered), ``False`` (definitively not), or ``None``
    when the check itself could not be completed -- a transport error or an
    unreadable payload, which must not be mistaken for a negative answer.
    """
    endpoint = rule.exists_url.format(**fields)
    response = await request("GET", endpoint)
    status = response.status_code if response is not None else None
    responses = [(endpoint, status)]
    if response is None:
        return None, responses
    if status in {404, 410}:
        return False, responses
    if status != 200:
        return None, responses
    try:
        payload = response.json()
    except ValueError:
        return None, responses
    count = _count_at_path(payload, rule.exists_count_path)
    if count is None:
        return None, responses
    return count > 0, responses


async def _probe_name(
    rule: Rule, fields: dict[str, str], *, request: RequestFn,
) -> tuple[str | None, list[tuple[str, int | None]]]:
    responses: list[tuple[str, int | None]] = []
    for template in (rule.name_url,):
        endpoint = template.format(**fields)
        response = await request("GET", endpoint)
        status = response.status_code if response is not None else None
        responses.append((endpoint, status))
        if response is None or status in {404, 410}:
            continue
        if status != 200:
            break
        try:
            name = _company_name(response.json())
        except ValueError:
            name = None
        if name:
            return name, responses
    return None, responses


async def _probe_comeet(
    rule: Rule, token: str, *, request: RequestFn, canonical_url: str = "",
) -> tuple[str, int | None, dict[str, Any]]:
    """Resolve Comeet's public board locator into its adapter input.

    Discovery sees ``slug/board-id`` in public URLs. Comeet's jobs API
    additionally requires a public per-board API token embedded in that
    board page. A successful probe promotes the candidate token to
    ``board-id/api-token``, which is sufficient to configure the existing
    Comeet adapter (key plus config.token after splitting once on ``/``).
    """
    parts = token.split("/", 1)
    if len(parts) != 2:
        return "inconclusive", None, {"responses": [], "reason": "invalid_comeet_token"}
    first, second = parts
    if len(second) >= 16 and second.isalnum():
        board_id, api_token = first, second
        board_response: httpx.Response | None = None
        board_url = canonical_url
    else:
        slug, board_id = first, second
        board_url = rule.board_url.format(**rule.urls(f"{slug}/{board_id}"))
        board_response = await request("GET", board_url)
        board_status = board_response.status_code if board_response is not None else None
        if board_response is None:
            return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": "transport"}
        if board_status in {404, 410}:
            return "absent", None, {"responses": [(board_url, board_status)]}
        if board_status != 200:
            return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": f"http_{board_status}"}
        match = COMEET_API_TOKEN.search(board_response.text)
        if match is None:
            return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": "missing_comeet_api_token"}
        api_token = match.group(1)

    endpoint = rule.probe_url.format(**rule.urls(f"x/{board_id}"))
    response = await request(
        "GET", endpoint + "?" + urlencode({"token": api_token, "details": "true"}),
    )
    status = response.status_code if response is not None else None
    responses = ([(board_url, board_response.status_code)] if board_response is not None else []) + [(endpoint, status)]
    if response is None:
        return "inconclusive", None, {"responses": responses, "reason": "transport"}
    if status in {404, 410}:
        return "absent", None, {"responses": responses}
    if status != 200:
        return "inconclusive", None, {"responses": responses, "reason": f"http_{status}"}
    try:
        payload = response.json()
    except ValueError:
        return "inconclusive", None, {"responses": responses, "reason": "invalid_json"}
    count = _job_count(payload)
    if count is None:
        return "inconclusive", None, {"responses": responses, "reason": "unrecognized_json"}
    details: dict[str, Any] = {
        "responses": responses, "_comeet_adapter_token": f"{board_id}/{api_token}",
    }
    company_name = _company_name(payload)
    if company_name:
        details.update(company_name=company_name, company_name_source="probe_json")
    return "live", count, details


async def _html_control(
    rule: Rule, fields: dict[str, str], *, request: RequestFn, controls: dict[str, str | None],
) -> str | None:
    key = rule.source
    if key in controls:
        return controls[key]
    control_fields = dict(fields)
    invalid = "keyscan-token-does-not-exist-9f4d2a"
    control_fields["token"] = invalid
    parts = sorted(int(name[1:]) for name in fields if name.startswith("t") and name[1:].isdigit())
    if parts:
        # Composite locators put a regional host in the first component.
        # Preserve that host and invalidate only the actual site/company
        # component, otherwise a generic-page control cannot be fetched.
        control_fields[f"t{parts[-1]}"] = invalid
    endpoint = rule.board_url.format(**control_fields)
    response = await request("GET", endpoint)
    value = None
    if response is not None and response.status_code == 200:
        value = _html_fingerprint(response.text[:20000].lower(), control_fields["token"])
    controls[key] = value
    return value
