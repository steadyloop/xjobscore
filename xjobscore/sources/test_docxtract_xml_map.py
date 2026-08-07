"""xml_map-shaped docxtract boards, against a saved vendor payload.

Personio is the reason `_xml_map` exists: `<workzag-jobs><position>` is
neither RSS nor Atom -- no namespace, and its own tag names throughout
(title is `name`, location is `office`). No `<url>` element exists on a
position at all, so posting_url must be templated, the same shape Vincere
needed for schema.org JSON-LD.
"""

from __future__ import annotations

from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract


FIXTURES = Path(__file__).parents[1] / "fixtures"


class FixtureNet:
    """Serves one saved body; records what was requested."""

    def __init__(self, body: bytes, *, url: str = "https://fixture.invalid/xml") -> None:
        self.response = Response(url=url, status=200, headers={}, body=body)
        self.get_calls: list[dict] = []

    async def get(self, url, *, params=None, headers=None, state=None):
        self.get_calls.append({"url": url, "params": params, "state": state})
        return self.response


def feed_for(key: str, source: str = "personio") -> Feed:
    return Feed(id=1, source=source, key=key, config={}, state={})


def personio_net() -> FixtureNet:
    return FixtureNet((FIXTURES / "personio.xml").read_bytes())


async def test_personio_harvest_demuxes_the_bespoke_feed():
    """One GET yields one Item per <position>, keyed by <id>."""
    net = personio_net()
    feed = feed_for("personio")

    record = await docxtract.harvest("personio", feed, net)

    assert net.get_calls[0]["url"] == "https://personio.jobs.personio.de/xml"
    assert record.http_status == 200
    assert [item.external_id for item in record.items] == ["1834171"]
    assert all(item.kind == "xml" for item in record.items)


async def test_personio_parse_templates_url_and_reads_bespoke_field_names():
    """No <url> element exists on a position -- posting_url is synthesized.
    Field names are Personio's own (name, office, subcompany), not RSS/Atom's."""
    net = personio_net()
    feed = feed_for("personio")
    record = await docxtract.harvest("personio", feed, net)

    posting = docxtract.parse("personio", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Staff Software Engineer, Data Platform"
    assert posting.url_posting == "https://personio.jobs.personio.de/job/1834171"
    assert posting.company_raw == "Personio SE & Co. KG"
    assert posting.location_raw == "Munich"
    assert posting.locations_extra == ["Berlin"]
    assert posting.employment_type == "permanent"
    assert posting.published_at is not None
    assert posting.published_at_precision == "exact"
    assert posting.source_extra["department"] == "Product and Tech"
    assert posting.source_extra["seniority"] == "experienced"


async def test_personio_empty_description_stays_none_not_empty_string():
    """jobDescriptions is present but empty on this live posting -- _text's
    strip-then-None must keep description_html unset, not a blank string."""
    net = personio_net()
    feed = feed_for("personio")
    record = await docxtract.harvest("personio", feed, net)

    posting = docxtract.parse("personio", feed, record.items[0], record.envelope)[0]

    assert posting.description_html is None


async def test_personio_is_registered_with_a_parser():
    assert "personio" in docxtract.SPECS
    assert callable(docxtract.harvester("personio"))
    assert callable(docxtract.parser("personio"))
