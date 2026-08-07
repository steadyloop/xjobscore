"""Atom-shaped docxtract boards, against a saved vendor payload.

PeopleAdmin is the reason `_atom` exists as its own function rather than a
flag on `_rss`: every base field (title, link, id, content, published) lives
in the Atom namespace, where RSS 2.0 leaves those unprefixed. The URL is also
an attribute (link/@href) rather than element text.
"""

from __future__ import annotations

from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract


FIXTURES = Path(__file__).parents[1] / "fixtures"


class FixtureNet:
    """Serves one saved body; records what was requested."""

    def __init__(self, body: bytes, *, url: str = "https://fixture.invalid/postings/search.atom") -> None:
        self.response = Response(url=url, status=200, headers={}, body=body)
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, state=None):
        self.get_calls.append({"url": url, "params": params, "state": state})
        return self.response


def feed_for(key: str, source: str = "peopleadmin") -> Feed:
    return Feed(id=1, source=source, key=key, config={}, state={})


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
    assert "peopleadmin" in docxtract.SPECS
    assert callable(docxtract.harvester("peopleadmin"))
    assert callable(docxtract.parser("peopleadmin"))
