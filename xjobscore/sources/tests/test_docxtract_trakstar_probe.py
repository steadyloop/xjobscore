"""Trakstar: a HEAD-probe substitute for a validator it doesn't have.

Verified live against real tenants (njohsp, moengage): Trakstar sends no
ETag, and ignores `If-Modified-Since` outright -- a conditional GET there
always returns 200 with the full ~37KB body. It does support `HEAD` and
returns a real `Last-Modified` there, day-granular and tied to the newest
posting, so a cheap probe still catches a dormant board before paying for
the body. `docxtract.harvest`'s `probe` branch is opt-in per source
(`docxtract-configs.py`'s trakstar entry) and only engages once a feed has a
stored `last_modified` from a previous poll -- a cold feed always falls
through to the real GET, exactly as it did before this existed.
"""

from __future__ import annotations

import pytest

from xjobscore.net import NetError, Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract

pytestmark = pytest.mark.asyncio

URL = "https://demo.hire.trakstar.com/jobfeeds/demo"
XML = b'''<rss xmlns:j="https://recruiterbox.com/rss/job/"><channel><item>
  <guid>t1</guid><title>Research Intern</title><link>https://example/t1</link>
  <description>&lt;p&gt;Research&lt;/p&gt;</description><j:locationCity>Dallas</j:locationCity>
  <j:positionType>Internship</j:positionType><j:team>R&amp;D</j:team>
</item></channel></rss>'''
STALE_LAST_MODIFIED = "Wed, 12 Aug 2026 18:30:00 GMT"
FRESH_LAST_MODIFIED = "Sat, 22 Aug 2026 17:16:00 GMT"


class ProbeNet:
    """A fake `Net` distinguishing HEAD from GET, the one thing every other
    fake in this tree (built around a plain GET/POST contract) doesn't do."""

    def __init__(self, *, head_last_modified: str | None, head_error: bool = False):
        self.head_last_modified = head_last_modified
        self.head_error = head_error
        self.head_calls = 0
        self.get_calls = 0

    async def head(self, url):
        self.head_calls += 1
        if self.head_error:
            raise NetError("boom", url=url, status=503)
        headers = {"last-modified": self.head_last_modified} if self.head_last_modified else {}
        return Response(url=url, status=200, headers=headers, body=b"")

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls += 1
        return Response(url=url, status=200, headers={"last-modified": FRESH_LAST_MODIFIED}, body=XML)


def feed(**kwargs) -> Feed:
    return Feed(id=1, source="trakstar", key="demo", **kwargs)


async def test_trakstar_config_opts_into_the_probe():
    assert docxtract.CONFIGS["trakstar"]["list"]["probe"] == {"method": "HEAD"}


async def test_cold_feed_skips_the_probe_and_goes_straight_to_get():
    """No stored value yet -- there is nothing to compare a HEAD against, so
    this must behave exactly as it did before the probe existed."""
    net = ProbeNet(head_last_modified=FRESH_LAST_MODIFIED)

    record = await docxtract.harvest("trakstar", feed(), net)

    assert net.head_calls == 0
    assert net.get_calls == 1
    assert record.last_modified == FRESH_LAST_MODIFIED
    assert len(record.items) == 1


async def test_matching_probe_skips_the_get_entirely():
    net = ProbeNet(head_last_modified=STALE_LAST_MODIFIED)

    record = await docxtract.harvest("trakstar", feed(last_modified=STALE_LAST_MODIFIED), net)

    assert net.head_calls == 1
    assert net.get_calls == 0
    assert record.count_unchanged is True
    assert record.last_modified == STALE_LAST_MODIFIED
    assert record.items == []


async def test_changed_probe_falls_through_to_a_real_get():
    net = ProbeNet(head_last_modified=FRESH_LAST_MODIFIED)

    record = await docxtract.harvest("trakstar", feed(last_modified=STALE_LAST_MODIFIED), net)

    assert net.head_calls == 1
    assert net.get_calls == 1
    assert record.count_unchanged is False
    assert record.last_modified == FRESH_LAST_MODIFIED
    assert len(record.items) == 1


async def test_a_failed_probe_falls_through_to_a_real_get_rather_than_failing_the_poll():
    """The probe is an optimization, not the authoritative fetch -- a 503,
    a timeout, or a vendor that stops answering HEAD must not cost the poll
    its whole result."""
    net = ProbeNet(head_last_modified=None, head_error=True)

    record = await docxtract.harvest("trakstar", feed(last_modified=STALE_LAST_MODIFIED), net)

    assert net.head_calls == 1
    assert net.get_calls == 1
    assert len(record.items) == 1
