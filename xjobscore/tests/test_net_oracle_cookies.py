"""Exercise real HTTPX cookie handling without making network requests."""

import httpx
import pytest

from xjobscore import net as net_module
from xjobscore.console.network import ConsoleNet
from xjobscore.net import Net


@pytest.fixture(params=[Net, ConsoleNet])
def client_factory(request, monkeypatch):
    original_client = httpx.AsyncClient

    def create(handler):
        monkeypatch.setattr(
            net_module.httpx,
            "AsyncClient",
            lambda **kwargs: original_client(
                **kwargs, transport=httpx.MockTransport(handler)
            ),
        )
        return request.param(user_agent="test", min_interval_s=0, max_retries=0)

    return create


async def test_oracle_tenant_cookies_do_not_overflow_feed_requests(client_factory):
    def respond(request):
        if request.url.path == "/career":
            tenant = request.url.host.split(".")[0]
            return httpx.Response(200, headers={
                "set-cookie": f"ORA_FND_SESSION_{tenant}_F={'a' * 93}; "
                              "Domain=.oraclecloud.com; Path=/; Secure"
            })
        return httpx.Response(
            400 if len(request.headers.get("cookie", "")) > 16000 else 200
        )

    async with client_factory(respond) as net:
        for i in range(216):
            await net.get(f"https://tenant{i}.oraclecloud.com/career")
        response = await net.get("https://tenant0.oraclecloud.com/feed")
        assert response.status == 200
        cookies = net._client.cookies.jar
        assert len(cookies) == 216
        assert all(cookie.domain != ".oraclecloud.com" for cookie in cookies)


async def test_oracle_redirects_keep_own_cookies_and_exclude_other_tenants(client_factory):
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path == "/career":
            return httpx.Response(302, headers=[
                ("location", "/landing"),
                ("set-cookie", "ORA_FUSION_PREFS=en; Domain=.oraclecloud.com; Path=/; Secure"),
                ("set-cookie", "ORA_FND_SESSION_TEST_F=session; Domain=oraclecloud.com; Path=/; Secure"),
                ("set-cookie", "JSESSIONID=local; Path=/; Secure"),
            ])
        return httpx.Response(200)

    async with client_factory(respond) as net:
        await net.get("https://tenant0.oraclecloud.com/career")
        own = seen[-1].headers["cookie"]
        assert "ORA_FUSION_PREFS=en" in own
        assert "ORA_FND_SESSION_TEST_F=session" in own
        assert "JSESSIONID=local" in own
        await net.get("https://tenant1.oraclecloud.com/feed")
        assert "cookie" not in seen[-1].headers
        await net.get("https://tenant0.oraclecloud.com/feed")
        assert seen[-1].headers["cookie"] == own
        await net.get("http://tenant0.oraclecloud.com/feed")
        assert "cookie" not in seen[-1].headers


@pytest.mark.parametrize("domain", [
    "example.com", "notoraclecloud.com", "oraclecloud.com.evil.example",
])
async def test_other_vendors_keep_existing_domain_cookie_behavior(client_factory, domain):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, headers={
            "set-cookie": f"session=original; Domain=.{domain}; Path=/"
        })

    async with client_factory(respond) as net:
        await net.get(f"https://tenant0.{domain}/career")
        await net.get(f"https://tenant1.{domain}/feed")
        assert seen[-1].headers["cookie"] == "session=original"


async def test_foreign_hosts_cannot_set_oracle_cookies(client_factory):
    def respond(request):
        return httpx.Response(200, headers={
            "set-cookie": "session=foreign; Domain=.oraclecloud.com; Path=/"
        })

    async with client_factory(respond) as net:
        await net.get("https://example.com/career")
        assert len(net._client.cookies.jar) == 0
