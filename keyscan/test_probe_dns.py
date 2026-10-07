"""A dead tenant host, and SmartRecruiters' bare zero, as answers not unknowns."""

from __future__ import annotations

from functools import partial

import httpx
import pytest

from keyscan.probe import default_request, probe_rule
from keyscan.rules import BY_SOURCE

DOH_HOSTS = {"cloudflare-dns.com", "dns.google"}


def doh(status: int, *answer_types: int) -> httpx.Response:
    return httpx.Response(200, json={
        "Status": status,
        "Answer": [{"name": "x.", "type": t, "data": "1.2.3.4"} for t in answer_types],
    })


async def probe(source: str, token: str, handler, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        return await probe_rule(
            BY_SOURCE[source], token, request=partial(default_request, client), controls={}, **kwargs,
        )


def dead_host(resolver):
    """Every vendor request fails to connect; DoH answers via `resolver(request)`."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in DOH_HOSTS:
            return resolver(request)
        raise httpx.ConnectError("nodename nor servname provided, or not known", request=request)
    return handler


@pytest.mark.asyncio
async def test_nxdomain_on_both_resolvers_is_absent():
    asked: list[str] = []

    def resolver(request):
        asked.append(f"{request.url.host} {request.url.params['type']} {request.url.params['name']}")
        return doh(3)

    status, _, details = await probe("taleo", "bupa", dead_host(resolver))
    assert (status, details["reason"]) == ("absent", "no_such_host")
    assert sorted(asked) == [
        "cloudflare-dns.com A bupa.taleo.net", "cloudflare-dns.com AAAA bupa.taleo.net",
        "dns.google A bupa.taleo.net", "dns.google AAAA bupa.taleo.net",
    ]


@pytest.mark.asyncio
async def test_noerror_without_an_address_is_absent():
    """Compact denial: Cloudflare-hosted zones answer a missing name NOERROR + SOA."""
    status, _, details = await probe("eightfold", "airasia.eightfold.ai/airasia.com", dead_host(lambda r: doh(0, 6)))
    assert (status, details["reason"]) == ("absent", "no_such_host")


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", [
    lambda r: doh(0, 1),                                               # an A record
    lambda r: doh(0, 5, 28),                                           # CNAME to an AAAA
    lambda r: doh(3) if r.url.host == "cloudflare-dns.com" else doh(0, 1),  # providers disagree
    lambda r: doh(2),                                                  # SERVFAIL
    lambda r: httpx.Response(503),
    lambda r: httpx.Response(200, text="not json"),
])
async def test_anything_short_of_no_address_stays_inconclusive(resolver):
    status, _, details = await probe("taleo", "bupa", dead_host(resolver))
    assert (status, details["reason"]) == ("inconclusive", "transport")


@pytest.mark.asyncio
async def test_unreachable_resolvers_stay_inconclusive():
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    status, _, details = await probe("taleo", "bupa", handler)
    assert (status, details["reason"]) == ("inconclusive", "transport")


@pytest.mark.asyncio
async def test_only_the_asked_host_is_resolved_not_a_redirect_target():
    """teamtailor sends a custom-domain tenant to its own site; that site being
    dead says nothing about the teamtailor board."""
    resolved: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in DOH_HOSTS:
            resolved.append(request.url.params["name"])
            return doh(0, 1) if request.url.params["name"] == "acme.teamtailor.com" else doh(3)
        if request.url.host == "acme.teamtailor.com":
            return httpx.Response(301, headers={"location": "https://careers.acme.example/jobs"})
        raise httpx.ConnectError("dead custom domain", request=request)

    status, _, details = await probe("teamtailor", "acme", handler)
    assert (status, details["reason"]) == ("inconclusive", "transport")
    assert set(resolved) == {"acme.teamtailor.com"}


@pytest.mark.asyncio
async def test_a_dead_host_behind_an_exists_check_is_absent():
    status, _, details = await probe("oracle", "gone.fa.em2/cx", dead_host(lambda r: doh(3)))
    assert (status, details["reason"]) == ("absent", "no_such_host")


def smartrecruiters(departments):
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"offset": 0, "limit": 1, "totalFound": 0, "content": []})
        if request.url.path.endswith("/departments"):
            return departments(request)
        return httpx.Response(404)
    return handler, asked


@pytest.mark.asyncio
async def test_smartrecruiters_unknown_company_is_absent():
    handler, asked = smartrecruiters(lambda r: httpx.Response(404, json={"httpCode": 404, "code": "RESOURCE_NOT_FOUND"}))
    status, count, details = await probe("smartrecruiters", "nope", handler, liveness_only=True)
    assert (status, count, details["reason"]) == ("absent", None, "company_not_found")
    assert asked == ["/v1/companies/nope/postings", "/v1/companies/nope/departments"]


@pytest.mark.asyncio
async def test_smartrecruiters_real_company_without_jobs_is_live():
    handler, _ = smartrecruiters(lambda r: httpx.Response(200, json={"totalFound": 0, "content": []}))
    status, count, details = await probe("smartrecruiters", "visa", handler, liveness_only=True)
    assert (status, count, details["reason"]) == ("live", 0, "company_without_jobs")


@pytest.mark.asyncio
@pytest.mark.parametrize(("departments", "reason"), [
    (lambda r: httpx.Response(429), "http_429"),
    (lambda r: httpx.Response(500), "zero_count"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)), "transport"),
])
async def test_smartrecruiters_unsettled_check_stays_inconclusive(departments, reason):
    handler, _ = smartrecruiters(departments)
    status, _, details = await probe("smartrecruiters", "acme", handler, liveness_only=True)
    assert (status, details["reason"]) == ("inconclusive", reason)


@pytest.mark.asyncio
async def test_smartrecruiters_with_jobs_skips_the_check():
    asked: list[str] = []

    def handler(request):
        asked.append(request.url.path)
        return httpx.Response(200, json={"totalFound": 3, "content": [{}]})

    status, count, _ = await probe("smartrecruiters", "acme", handler, liveness_only=True)
    assert (status, count) == ("live", 3)
    assert asked == ["/v1/companies/acme/postings"]
