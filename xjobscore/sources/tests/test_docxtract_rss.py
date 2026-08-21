"""RSS-shaped docxtract boards, against saved vendor payloads.

SuccessFactors is the reason `_rss` grew `company_path` and a flat-location
fallback, so both shapes are asserted here: Teamtailor's nested
`<location><name>` must keep working, and the two SuccessFactors fields that
look useful but are not (`g:expiration_date`, absent `pubDate`) must stay out
of `deadline_at` and `published_at`.
"""

from __future__ import annotations

from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract


FIXTURES = Path(__file__).parents[2] / "fixtures"


class FixtureNet:
    """Serves one saved body; records what was requested."""

    def __init__(self, body: bytes, *, url: str = "https://fixture.invalid/sitemap.xml") -> None:
        self.response = Response(url=url, status=200, headers={}, body=body)
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "etag": etag})
        return self.response


def feed_for(key: str, source: str = "successfactors") -> Feed:
    return Feed(id=1, source=source, key=key, config={})


def successfactors_net() -> FixtureNet:
    return FixtureNet((FIXTURES / "successfactors.xml").read_bytes())


async def test_successfactors_harvest_demuxes_the_rss_feed():
    """One GET at the tenant host yields one Item per job, keyed by guid."""
    net = successfactors_net()
    feed = feed_for("jobs.sap.com")

    record = await docxtract.harvest("successfactors", feed, net)

    assert net.get_calls[0]["url"] == "https://jobs.sap.com/sitemap.xml"
    assert record.http_status == 200
    assert record.truncated is False
    assert [item.external_id for item in record.items] == ["1415037233", "1404797833"]
    # payload is the whole <item> element, so bronze stays replayable. ET
    # rewrites the g: prefix to ns0: on serialization -- the namespace URI and
    # every value survive, which is what replay actually depends on.
    assert all(item.kind == "xml" for item in record.items)
    assert 'xmlns:ns0="http://base.google.com/ns/1.0"' in record.items[0].payload
    assert "<ns0:employer>SAP</ns0:employer>" in record.items[0].payload


async def test_successfactors_parse_reads_employer_and_flat_location():
    """company_raw comes from g:employer, not feed.key; g:location is flat text."""
    net = successfactors_net()
    feed = feed_for("jobs.sap.com")
    record = await docxtract.harvest("successfactors", feed, net)

    posting = docxtract.parse("successfactors", feed, record.items[0], record.envelope)[0]

    assert posting.company_raw == "SAP"  # not "jobs.sap.com"
    assert posting.location_raw == "Atlanta, GA, US, 30326"
    assert posting.title.startswith("Customer Success Manager (CSM)")
    assert posting.url_posting.startswith("https://jobs.sap.com/job/Atlanta-")
    assert posting.external_id == "1415037233"


async def test_successfactors_states_no_apply_url():
    """The feed carries no apply URL and none is synthesized.

    /talentcommunity/apply/{guid}/ appears on the job page and looks derivable,
    but requested directly it 302s to the career-site homepage -- a nonexistent
    id does the same, so its 200 is not evidence. The real target is JS-injected
    behind a per-session CSRF token. Leaving the field None keeps url_posting
    the honest apply entry point instead of shipping a link to a homepage.
    """
    net = successfactors_net()
    feed = feed_for("jobs.sap.com")
    record = await docxtract.harvest("successfactors", feed, net)

    postings = [
        docxtract.parse("successfactors", feed, item, record.envelope)[0]
        for item in record.items
    ]

    for posting in postings:
        assert posting.apply_url is None
        assert posting.url_posting.startswith("https://jobs.sap.com/job/")
    # nothing in the feed itself offers one either
    assert "apply" not in record.items[0].payload.lower()
    # one GET total: no detail fetch was attempted to go looking for it
    assert len(net.get_calls) == 1


async def test_successfactors_description_is_html_not_escaped_text():
    """The feed CDATA-wraps entity-escaped markup, so it needs exactly one
    unescape: none leaves '&lt;p&gt;' as literal text, two would decode
    entities that belong inside attribute values."""
    net = successfactors_net()
    feed = feed_for("jobs.sap.com")
    record = await docxtract.harvest("successfactors", feed, net)

    description = docxtract.parse(
        "successfactors", feed, record.items[0], record.envelope
    )[0].description_html

    assert description.startswith("<p style=")
    assert "&lt;" not in description
    # one pass, not two: the entity inside the style attribute must survive
    assert "&#39;Times New Roman&#39;" in description


async def test_successfactors_drops_the_fields_that_would_lie():
    """g:expiration_date is a feed TTL and there is no pubDate: neither may
    become a Posting date. Both stay readable in source_extra instead."""
    net = successfactors_net()
    feed = feed_for("jobs.sap.com")
    record = await docxtract.harvest("successfactors", feed, net)

    postings = [
        docxtract.parse("successfactors", feed, item, record.envelope)[0]
        for item in record.items
    ]

    # every item in a real feed carries the same expiration_date, which is what
    # makes it a TTL rather than a deadline
    stamps = {posting.source_extra["expiration_date"] for posting in postings}
    assert len(stamps) == 1

    for posting in postings:
        assert posting.deadline_at is None
        assert posting.published_at is None
        assert posting.published_at_precision == "unknown"
        assert posting.source_extra["employer"] == "SAP"


async def test_successfactors_is_registered_with_a_parser():
    assert "successfactors" in docxtract.SPECS
    assert callable(docxtract.harvester("successfactors"))
    assert callable(docxtract.parser("successfactors"))


async def test_nested_location_shape_still_resolves():
    """The flat-location fallback must not break Teamtailor's <location><name>."""
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><item>
<title>Backend Engineer</title>
<link>https://acme.teamtailor.com/jobs/1</link>
<guid>1</guid>
<tt:locations><tt:location><tt:name>Stockholm</tt:name></tt:location>
<tt:location><tt:name>Oslo</tt:name></tt:location></tt:locations>
<tt:department>Engineering</tt:department>
<description><![CDATA[use &lt;b&gt; for bold]]></description>
</item></channel></rss>"""
    net = FixtureNet(body)
    feed = feed_for("acme", source="teamtailor")

    record = await docxtract.harvest("teamtailor", feed, net)
    posting = docxtract.parse("teamtailor", feed, record.items[0], record.envelope)[0]

    assert posting.location_raw == "Stockholm"
    assert posting.locations_extra == ["Oslo"]
    # no company_path configured, so the key still names the employer
    assert posting.company_raw == "acme"
    assert posting.source_extra["department"] == "Engineering"
    # unescape_description is opt-in. This description CDATA-wraps an escaped
    # sequence a job author typed as prose; without the flag it stays prose
    # instead of silently becoming a tag.
    assert posting.description_html == "use &lt;b&gt; for bold"
    assert posting.apply_url is None


async def test_successfactors_per_feed_url_override_is_honoured():
    """One tenant (NextEra) serves a real sitemap at /sitemap.xml -- a <urlset>
    of bare job URLs that the shared `channel/item` path parses to zero items,
    silently. Its RSS lives at /rss.xml, so that feed overrides the template
    URL; the others must keep using it.
    """
    net = successfactors_net()
    feed = Feed(
        id=1, source="successfactors", key="jobs.nexteraenergy.com",
        config={"url": "https://jobs.nexteraenergy.com/rss.xml"},
    )

    record = await docxtract.harvest("successfactors", feed, net)

    assert net.get_calls[0]["url"] == "https://jobs.nexteraenergy.com/rss.xml"
    assert [item.external_id for item in record.items] == ["1415037233", "1404797833"]


async def test_successfactors_sitemap_shape_yields_nothing():
    """The failure this override exists for: a <urlset> parses clean and
    returns zero items, so a broken feed looks like an empty board rather than
    an error. Pinned so the shape is recognisable if it turns up again.
    """
    sitemap = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<urlset xmlns="http://www.google.com/schemas/sitemap/0.9">'
        b"<url><loc>https://jobs.nexteraenergy.com/job/x/1415396900/</loc>"
        b"<lastmod>2026-08-19</lastmod></url></urlset>"
    )
    feed = feed_for("jobs.nexteraenergy.com")

    record = await docxtract.harvest("successfactors", feed, FixtureNet(sitemap))

    assert record.http_status == 200
    assert record.items == []
