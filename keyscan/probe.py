"""Standalone probe engine, shared by the crawl and by callers that only need
to classify a single token's liveness (e.g. the console's Tokens page).

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

import asyncio
import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode, urlsplit

import httpx

from xjobscore import workable_policy

from .rules import Rule
# Pure helpers stay defined in core; import them here to keep one source of
# truth. core imports this module lazily (inside Keyscan._probe) to avoid a
# circular import at load time.
from .core import (
    COMEET_API_TOKEN,
    WAF_MARKERS,
    _company_name,
    _count_at_path,
    _html_company_name,
    _html_fingerprint,
    _job_count,
    _xml_job_count,
)

# request(method, url, *, json_body=None, headers=None) -> httpx.Response | None
RequestFn = Callable[..., Awaitable["httpx.Response | None"]]


class HTMLControlCache(dict[str, str | None]):
    """Reuse answered HTML controls and share concurrent requests for one."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: dict[str, asyncio.Task[str | None]] = {}


class _HTMLControlFailure(Exception):
    def __init__(self, endpoint: str, status: int | None, reason: str) -> None:
        super().__init__(reason)
        self.response = (endpoint, status)
        self.reason = reason


_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# DNS-over-HTTPS resolvers that decide whether a host that would not connect
# exists at all. The OS resolver cannot: macOS `getaddrinfo` raises the same
# EAI_NONAME for NXDOMAIN, SERVFAIL and a resolver timeout (Libinfo's
# mdns_module.c; measured 2026-10-06 -- dnssec-failed.org, servfail.nl and
# brokendnssec.net all errno 8), and a VPN's local resolver may rewrite
# answers. A DoH JSON answer carries the real rcode. Both providers must say
# "no address" before a host counts as gone.
_DOH_URLS = (
    "https://cloudflare-dns.com/dns-query?name={host}&type={rtype}",
    "https://dns.google/resolve?name={host}&type={rtype}",
)
_DOH_HEADERS = {"accept": "application/dns-json"}
# Record types whose presence means the host has an address.
_ADDRESS_TYPES = {"A": 1, "AAAA": 28}
_NOERROR, _NXDOMAIN = 0, 3


async def default_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response | None:
    """A minimal :data:`RequestFn` over an existing client.

    Retries transient failures up to three times. Workable verification uses
    shared pacing, and its first 429 persists a vendor cooldown and returns
    immediately. Other hosts have no additional pacing or timing logs here.
    """
    response: httpx.Response | None = None
    for attempt in range(3):
        try:
            await workable_policy.before_request(url, validation=True)
        except workable_policy.CoolingDown as exc:
            return workable_policy.deferred_response(url, exc)
        try:
            response = await client.request(method, url, json=json_body, headers=headers)
        except (httpx.TimeoutException, httpx.HTTPError):
            response = None
        else:
            if await workable_policy.note_response(response):
                return response
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
    cache of generic-page fingerprints by control URL; pass a dict to reuse
    it across calls, or ``HTMLControlCache`` to also share in-flight requests.
    Leave it ``None`` for a fresh one.

    ``liveness_only`` narrows the question to "does this board exist" and lets
    a rule swap in its cheap variant (``light_url`` / ``light_method``). The
    crawl leaves it off because it persists ``job_count``; the console's
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
            endpoint, status = exists_responses[-1]
            if status is None and await _host_gone(endpoint, request=request):
                return "absent", None, {"responses": exists_responses, "reason": "no_such_host"}
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
        # A tenant host that no longer resolves is the board's answer, not a
        # network fault -- every vendor that puts the token in the host
        # (avature, taleo, eightfold, icims, oracle, ...) loses a dead
        # tenant's DNS. Only the host we asked for counts: a redirect onward
        # to a dead custom domain says nothing about the vendor's board.
        if await _host_gone(endpoint, request=request):
            return "absent", None, {"responses": responses, "reason": "no_such_host"}
        return "inconclusive", None, {"responses": responses, "reason": "transport"}
    if all(status in {404, 410} for _, status in responses):
        return "absent", None, {"responses": responses}
    if (
        response.status_code != 200
        and rule.absent_error_codes
        and _error_code(response) in rule.absent_error_codes
    ):
        return "absent", None, {"responses": responses, "reason": "site_disabled"}
    if response.status_code == 422 and rule.shards:
        return await _probe_shards(rule, token, method, primary, responses, request=request)
    if response.status_code != 200:
        reason = _refusal_reason(response)
        details = {"responses": responses, "reason": reason}
        # A refusal after leaving the vendor's domains belongs to that one
        # tenant, not the entire ATS. Keep the final URL across both httpx and
        # the sweep's Net adapter so a redirected employer 429 cannot park
        # thousands of unrelated boards.
        try:
            final_url = str(response.url)
        except (AttributeError, RuntimeError):  # response doubles without a request
            final_url = endpoint
        final_host = urlsplit(final_url).hostname
        if (
            reason in {"http_429", "blocked"}
            and rule.domains
            and final_host
            and final_host != urlsplit(endpoint).hostname
            and not any(final_host == domain or final_host.endswith("." + domain) for domain in rule.domains)
        ):
            details.update(refusal_scope="token", final_url=final_url)
        return "inconclusive", None, details
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
        if count == 0 and not company_name and not rule.zero_is_live:
            # A zero total is ambiguous: some APIs (SmartRecruiters'
            # postings search, at least) return this identical shape for
            # a nonexistent company id and for a real one with no open
            # postings right now, with no distinguishing metadata either
            # way. Without a company name to confirm the id resolved to
            # something real, don't count a bare zero as evidence of life.
            if rule.zero_check_url:
                verdict = await _probe_zero_check(rule, fields, details, request=request)
                if verdict is not None:
                    return verdict, (0 if verdict == "live" else None), details
            details.setdefault("reason", "zero_count")
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
    if rule.source == "paylocity":
        return _paylocity_board(response.text, token, responses)
    try:
        control = await _html_control(rule, fields, request=request, controls=controls)
    except _HTMLControlFailure as exc:
        # A refused control is no evidence that the candidate differs from a
        # nonexistent board. Surface its refusal so the sweep cools down and
        # leaves the token unchecked instead of recording a false live.
        return "inconclusive", None, {
            "responses": [*responses, exc.response],
            "reason": exc.reason, "stage": "html_control",
        }
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


def _paylocity_board(
    body: str, token: str, responses: list[tuple[str, int | None]],
) -> tuple[str, int | None, dict[str, Any]]:
    """The server-rendered board carries its identity and jobs in pageData.

    Both nonexistent IDs and real boards answer HTTP 200. Parse the board's
    actual data instead of comparing an additional negative-control page.
    The lead-join locator is present even when joining is disabled and ties
    the payload to this board; an empty Jobs array still proves existence.
    """
    details: dict[str, Any] = {"responses": responses}
    match = re.search(r"\bwindow\.pageData\s*=\s*", body)
    try:
        payload, _ = json.JSONDecoder().raw_decode(body, match.end()) if match else (None, 0)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("Jobs"), list):
        return "inconclusive", None, {**details, "reason": "missing_board_data"}
    locator = payload.get("LeadJoinUrl")
    if (
        not isinstance(locator, str)
        or urlsplit(locator).path.rstrip("/").lower() != f"/recruiting/publicleads/new/{token}".lower()
    ):
        return "inconclusive", None, {**details, "reason": "board_identity_mismatch"}
    name = _html_company_name(body)
    if name:
        details.update(company_name=name, company_name_source="probe_html")
    return "live", len(payload["Jobs"]), {**details, "reason": "board_data"}


def _refusal_reason(response: httpx.Response) -> str:
    """``blocked`` for a 403 that is a wall refusing *us*, else ``http_<status>``.

    Mirrors `Keyscan._note_refusal`: on vendors that give every tenant its own
    host, most 403s are that one tenant's answer, and only a body naming a bot
    wall says the vendor is refusing us. A bodiless 403 (a HEAD probe) cannot
    say either way, so it is treated as the wall.
    """
    status = response.status_code
    if status == 403:
        body = response.text[:4000].lower()
        if not body or any(marker in body for marker in WAF_MARKERS):
            return "blocked"
    return f"http_{status}"


async def _probe_shards(
    rule: Rule, token: str, method: str, template: str,
    responses: list[tuple[str, int | None]], *, request: RequestFn,
) -> tuple[str, int | None, dict[str, Any]]:
    """Look for a 422'd tenant on the vendor's other shards.

    Workday answers 422 both for a tenant that does not exist and for a real
    one asked on the wrong ``wdN`` host, and a wrong host does not redirect.
    The token is ``absent`` either way -- it cannot be polled as written -- but
    when another shard answers 200, ``moved_to`` names the token that can.
    """
    parts = token.split("/")
    for shard in rule.shards:
        if shard == parts[1]:
            continue
        moved = "/".join([parts[0], shard, *parts[2:]])
        endpoint = template.format(**rule.urls(moved))
        response = await request(method, endpoint, json_body=rule.body, headers=rule.headers)
        status = response.status_code if response is not None else None
        responses.append((endpoint, status))
        if status == 422:
            continue
        if status == 200:
            return "absent", None, {"responses": responses, "reason": "wrong_shard", "moved_to": moved}
        if status in {404, 410} or (
            status is not None and _error_code(response) in rule.absent_error_codes
        ):
            # The tenant lives on this shard, but not the site.
            return "absent", None, {"responses": responses, "reason": "wrong_shard_no_site"}
        reason = "transport" if response is None else _refusal_reason(response)
        return "inconclusive", None, {"responses": responses, "reason": reason}
    return "absent", None, {"responses": responses, "reason": "tenant_not_found"}


def _error_code(response: httpx.Response) -> str | None:
    """The ``errorCode`` a JSON error body names, or None."""
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload.get("errorCode") if isinstance(payload, dict) else None


async def _host_gone(url: str, *, request: RequestFn) -> bool:
    """True only when DNS authoritatively gives `url`'s host no address.

    Asks every `_DOH_URLS` provider for A and AAAA. NXDOMAIN and a NOERROR
    answer without an address record both mean "no address" -- the second is
    what DNSSEC zones serving compact denial (Cloudflare's, RFC 9824) send for
    a name that does not exist, and how avature and eightfold answer a dead
    tenant. Any address, SERVFAIL, refusal, or a resolver that cannot be read
    is False: not knowing must never read as gone.
    """
    host = urlsplit(url).hostname
    if not host:
        return False
    for template in _DOH_URLS:
        for rtype, code in _ADDRESS_TYPES.items():
            endpoint = template.format(host=quote(host, safe=""), rtype=rtype)
            response = await request("GET", endpoint, headers=_DOH_HEADERS)
            if response is None or response.status_code != 200:
                return False
            try:
                payload = response.json()
            except ValueError:
                return False
            if not isinstance(payload, dict):
                return False
            rcode = payload.get("Status")
            if rcode == _NXDOMAIN:
                continue
            if rcode != _NOERROR:
                return False
            answers = payload.get("Answer") or []
            if not isinstance(answers, list) or any(
                not isinstance(answer, dict) or answer.get("type") == code for answer in answers
            ):
                return False
    return True


async def _probe_zero_check(
    rule: Rule, fields: dict[str, str], details: dict[str, Any], *, request: RequestFn,
) -> str | None:
    """Settle a bare zero count with `rule.zero_check_url`: absent, live, or None."""
    endpoint = rule.zero_check_url.format(**fields)
    response = await request("GET", endpoint)
    status = response.status_code if response is not None else None
    details["zero_check"] = (endpoint, status)
    if status in {404, 410}:
        details["reason"] = "company_not_found"
        return "absent"
    if status == 200:
        details["reason"] = "company_without_jobs"
        return "live"
    # Unsettled. A refusal keeps its own reason so a caller that retries
    # refusals (the token sweep) does not record this as checked.
    if response is None:
        details["reason"] = "transport"
    elif _refusal_reason(response) in {"http_429", "blocked"}:
        details["reason"] = _refusal_reason(response)
    return None


async def _probe_exists(
    rule: Rule, fields: dict[str, str], *, request: RequestFn,
) -> tuple[bool | None, list[tuple[str, int | None]]]:
    """Ask a board-scoped endpoint whether this token names a real board.

    Returns ``True`` (registered), ``False`` (definitively not), or ``None``
    when the check itself could not be completed -- a transport error or an
    unreadable payload, which must not be mistaken for a negative answer.

    ``exists_fallbacks`` are asked in turn only after a definite negative, so
    ``False`` means every lookup said no; any lookup that fails first ends the
    check as ``None``.
    """
    responses: list[tuple[str, int | None]] = []
    for template in (rule.exists_url, *rule.exists_fallbacks):
        endpoint = template.format(**fields)
        response = await request("GET", endpoint)
        status = response.status_code if response is not None else None
        responses.append((endpoint, status))
        if response is None:
            return None, responses
        if status in {404, 410}:
            continue
        if status != 200:
            return None, responses
        try:
            payload = response.json()
        except ValueError:
            return None, responses
        count = _count_at_path(payload, rule.exists_count_path)
        if count is None:
            return None, responses
        if count > 0:
            return True, responses
    return False, responses


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
            return "inconclusive", None, {"responses": [(board_url, board_status)], "reason": _refusal_reason(board_response)}
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
        return "inconclusive", None, {"responses": responses, "reason": _refusal_reason(response)}
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
    endpoint = _html_control_url(rule, fields)
    key = endpoint
    if key in controls:
        return controls[key]
    if not isinstance(controls, HTMLControlCache):
        return await _fetch_html_control(rule, endpoint, request=request, controls=controls)
    task = controls.pending.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_html_control(rule, endpoint, request=request, controls=controls),
            name=f"html-control-{key}",
        )
        controls.pending[key] = task
    try:
        return await task
    finally:
        if controls.pending.get(key) is task:
            del controls.pending[key]


def _html_control_url(rule: Rule, fields: dict[str, str]) -> str:
    control_fields = dict(fields)
    invalid = rule.html_control_token
    control_fields["token"] = invalid
    parts = sorted(int(name[1:]) for name in fields if name.startswith("t") and name[1:].isdigit())
    if parts:
        # Composite locators put a regional host in the first component.
        # Preserve that host and invalidate only the actual site/company
        # component, otherwise a generic-page control cannot be fetched.
        control_fields[f"t{parts[-1]}"] = invalid
    if len(parts) > 1 and "tail" in fields:
        # `{tail}` repeats the parts after the first, so it must carry the
        # same invalidated last part or the control fetches the real board.
        control_fields["tail"] = "/".join(
            [control_fields[f"t{index}"] for index in parts[1:]]
        )
    return rule.board_url.format(**control_fields)


async def _fetch_html_control(
    rule: Rule, endpoint: str, *, request: RequestFn, controls: dict[str, str | None],
) -> str | None:
    response = await request("GET", endpoint)
    value = None
    if response is None:
        raise _HTMLControlFailure(endpoint, None, "transport")
    if response.status_code == 200:
        body = response.text[:20000].lower()
        if "<html" not in body and "<!doctype" not in body:
            raise _HTMLControlFailure(endpoint, 200, "invalid_html_control")
        value = _html_fingerprint(body, rule.html_control_token)
    elif response.status_code not in {404, 410}:
        raise _HTMLControlFailure(endpoint, response.status_code, _refusal_reason(response))
    # Only answered controls are cached. A 429, wall, or failed connection must
    # be retried after recovery, rather than disabling comparisons all run.
    controls[endpoint] = value
    return value
