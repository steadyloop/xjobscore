"""Atom-shaped docxtract boards, against saved vendor payloads.

PeopleAdmin is the reason `_atom` exists as its own function rather than a
flag on `_rss`: every base field (title, link, id, content, published) lives
in the Atom namespace, where RSS 2.0 leaves those unprefixed. The URL is also
an attribute (link/@href) rather than element text.

Oleeo uses the same Atom core shape, but only tenants whose content type is
`text` are catalogable with the unchanged parser. A real `xhtml` entry pins
the unsupported shape so it cannot be mistaken for a descriptive feed.
"""

from __future__ import annotations

from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract


FIXTURES = Path(__file__).parents[3] / "fixtures"


class FixtureNet:
    """Serves one saved body; records what was requested."""

    def __init__(self, body: bytes, *, url: str = "https://fixture.invalid/postings/search.atom") -> None:
        self.response = Response(url=url, status=200, headers={}, body=body)
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, etag=None):
        self.get_calls.append({"url": url, "params": params, "etag": etag})
        return self.response


def feed_for(key: str, source: str = "peopleadmin") -> Feed:
    return Feed(id=1, source=source, key=key, config={})


def peopleadmin_net() -> FixtureNet:
    return FixtureNet((FIXTURES / "peopleadmin.atom").read_bytes())


async def test_peopleadmin_harvest_demuxes_the_atom_feed():
    """One GET at the tenant subdomain yields one Item per posting, keyed by <id>."""
    net = peopleadmin_net()
    feed = feed_for("marshall")

    record = await docxtract.harvest("peopleadmin", feed, net)

    assert net.get_calls[0]["url"] == "https://marshall.peopleadmin.com/postings/search.atom"
    assert record.http_status == 200
    assert record.truncated is False
    assert [item.external_id for item in record.items] == [
        "https://marshall.peopleadmin.com/postings/32641",
        "https://marshall.peopleadmin.com/postings/32632",
    ]
    assert all(item.kind == "xml" for item in record.items)


async def test_peopleadmin_parse_reads_link_href_not_element_text():
    """Atom's URL lives on link/@href -- there is no bare <link> text to read."""
    net = peopleadmin_net()
    feed = feed_for("marshall")
    record = await docxtract.harvest("peopleadmin", feed, net)

    posting = docxtract.parse("peopleadmin", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Campus Police Officer"
    assert posting.url_posting == "https://marshall.peopleadmin.com/postings/32641"
    assert posting.company_raw == "marshall"  # tenant key, not the hiring unit
    assert posting.source_extra["hiring_unit"] == "Public Safety - MU5500"
    assert posting.description_html.startswith("<div>Sworn officer")
    assert posting.published_at is not None
    assert posting.published_at_precision == "exact"


async def test_peopleadmin_content_needs_no_extra_unescape():
    """XML parsing already decodes the entity-escaped markup once; a second
    unescape() would wrongly decode entities meant to stay literal."""
    net = peopleadmin_net()
    feed = feed_for("marshall")
    record = await docxtract.harvest("peopleadmin", feed, net)

    description = docxtract.parse("peopleadmin", feed, record.items[1], record.envelope)[0].description_html

    assert "&lt;" not in description
    assert "<div>" in description


async def test_peopleadmin_is_registered_with_a_parser():
    assert "peopleadmin" in docxtract.CONFIGS
    assert callable(docxtract.harvester("peopleadmin"))
    assert callable(docxtract.parser("peopleadmin"))


def oleeo_net(fixture: str = "oleeo.atom") -> FixtureNet:
    return FixtureNet(
        (FIXTURES / fixture).read_bytes(),
        url="https://asp.tal.net/candidate/jobboard/vacancy/14/feed",
    )


def oleeo_feed() -> Feed:
    return Feed(
        id=1,
        source="oleeo",
        key="asp",
        config={"url": "https://asp.tal.net/candidate/jobboard/vacancy/14/feed"},
    )


async def test_oleeo_harvest_uses_board_url_override_and_demuxes_atom_entries():
    """Oleeo's board number is per-tenant, so non-/1/ feeds use feed.config."""
    net = oleeo_net()
    feed = oleeo_feed()

    record = await docxtract.harvest("oleeo", feed, net)

    assert net.get_calls[0]["url"] == "https://asp.tal.net/candidate/jobboard/vacancy/14/feed"
    assert record.http_status == 200
    assert record.truncated is False
    assert [item.external_id for item in record.items] == [
        "https://asp.tal.net/vx/mobile-0/appcentre-External/brand-0/candidate/so/pm/6/pl/24/opp/8084-D-Supt-Regional-Head-of-HUMINT-Transferee/en-GB",
        "https://asp.tal.net/vx/mobile-0/appcentre-External/brand-0/candidate/so/pm/6/pl/24/opp/7959-Technical-Support-Co-ordinator/en-GB",
    ]
    assert all(item.kind == "xml" for item in record.items)


async def test_oleeo_text_content_parses_without_adapter_changes():
    net = oleeo_net()
    feed = oleeo_feed()
    record = await docxtract.harvest("oleeo", feed, net)

    posting = docxtract.parse("oleeo", feed, record.items[1], record.envelope)[0]

    assert posting.title == "Technical Support Co-ordinator"
    assert posting.url_posting.endswith("/opp/7959-Technical-Support-Co-ordinator/en-GB?instant=apply")
    assert posting.company_raw == "asp"
    assert posting.description_html is not None
    assert posting.description_html.startswith("ID:7959<br />")
    assert "friendly IT team" in posting.description_html
    assert posting.location_raw is None  # location is only label-prefixed prose
    assert posting.deadline_at is None  # closing date is only label-prefixed prose
    assert posting.published_at is not None
    assert posting.published_at.isoformat() == "2026-08-06T15:00:00+00:00"
    assert posting.published_at_precision == "exact"


async def test_oleeo_xhtml_content_remains_explicitly_unsupported():
    """Do not catalog this feed as live until `_atom` can descend into XHTML."""
    net = oleeo_net("oleeo_xhtml.atom")
    feed = feed_for("environmentagencyjobs", source="oleeo")
    record = await docxtract.harvest("oleeo", feed, net)

    posting = docxtract.parse("oleeo", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Commercial Business Relationship Manager - 35799"
    assert posting.description_html is None
    assert "Salary Range:50,001 - 60,000" in record.items[0].payload


async def test_oleeo_is_registered_with_a_parser():
    assert "oleeo" in docxtract.CONFIGS
    assert callable(docxtract.harvester("oleeo"))
    assert callable(docxtract.parser("oleeo"))


def flyio_net(fixture: str = "flyio.atom") -> FixtureNet:
    return FixtureNet((FIXTURES / fixture).read_bytes(), url="https://fly.io/jobs/feed.xml")


async def test_flyio_harvest_demuxes_two_entries():
    """A static, single-tenant URL like unstop's -- key is inert here."""
    net = flyio_net()
    feed = feed_for("flyio", source="flyio")

    record = await docxtract.harvest("flyio", feed, net)

    assert net.get_calls[0]["url"] == "https://fly.io/jobs/feed.xml"
    assert record.http_status == 200
    assert record.truncated is False
    assert [item.external_id for item in record.items] == [
        "https://fly.io/jobs/software-engineer-platform/",
        "https://fly.io/jobs/site-reliability-engineer/",
    ]
    assert all(item.kind == "xml" for item in record.items)


async def test_flyio_parse_reads_atom_fields():
    net = flyio_net()
    feed = feed_for("flyio", source="flyio")
    record = await docxtract.harvest("flyio", feed, net)

    posting = docxtract.parse("flyio", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Software Engineer, Platform"
    assert posting.url_posting == "https://fly.io/jobs/software-engineer-platform/"
    assert posting.company_raw == "flyio"  # tenant key -- Fly.io names itself nowhere on an entry
    assert posting.description_html == "<p>We're looking for engineers who like operating systems, networking, and Rust.</p>"
    assert posting.published_at is not None
    assert posting.published_at_precision == "exact"


async def test_flyio_harvest_on_the_currently_empty_live_envelope_returns_no_items():
    """Fly.io has no open roles right now -- a valid state, not a broken feed."""
    net = flyio_net("flyio_empty.atom")
    feed = feed_for("flyio", source="flyio")

    record = await docxtract.harvest("flyio", feed, net)

    assert record.items == []
    assert record.truncated is False
    assert record.http_status == 200


async def test_flyio_is_registered_with_a_parser():
    assert "flyio" in docxtract.CONFIGS
    assert callable(docxtract.harvester("flyio"))
    assert callable(docxtract.parser("flyio"))
