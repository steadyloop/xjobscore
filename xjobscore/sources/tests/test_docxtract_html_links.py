"""html_links + jsonld docxtract boards, against saved vendor payloads.

Vincere and JazzHR both discover jobs through a list page's <a href> links
and read the actual posting off JSON-LD on each detail page -- neither
vendor's list page carries JobPosting JSON-LD itself. Vincere's JobPosting
block has no `url` of its own (posting_url is templated from key+id);
JazzHR's does, and also carries a real `identifier`-shaped url that a bare
`job.get("url")` fallback should surface untouched.
"""

from __future__ import annotations

from pathlib import Path

from xjobscore.net import Response
from xjobscore.records import Feed
from xjobscore.sources import docxtract


FIXTURES = Path(__file__).parents[3] / "fixtures"


class LinkFixtureNet:
    """Serves a list page, then a fixed detail page for every discovered link."""

    def __init__(self, list_body: bytes, detail_body: bytes, *, list_url: str) -> None:
        self.list_response = Response(url=list_url, status=200, headers={}, body=list_body)
        self.detail_response = Response(url="https://fixture.invalid/detail", status=200, headers={}, body=detail_body)
        self.get_calls: list[dict] = []

    # vincere and jazzhr both declare detail_concurrency now, so the fake has
    # to accept what the real Net accepts.
    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.get_calls.append({"url": url, "params": params, "etag": etag})
        if len(self.get_calls) == 1:
            return self.list_response
        return self.detail_response


def feed_for(key: str, source: str) -> Feed:
    return Feed(id=1, source=source, key=key, config={})


def vincere_net() -> LinkFixtureNet:
    return LinkFixtureNet(
        (FIXTURES / "vincere_board.html").read_bytes(),
        (FIXTURES / "vincere_job_page.html").read_bytes(),
        list_url="https://zealhr.vincere.io/careers/",
    )


def jazzhr_net() -> LinkFixtureNet:
    return LinkFixtureNet(
        (FIXTURES / "jazzhr_board.html").read_bytes(),
        (FIXTURES / "jazzhr_job_page.html").read_bytes(),
        list_url="https://dtexsystems.applytojob.com/apply/jobs/",
    )


async def test_vincere_harvest_discovers_ids_from_list_links():
    """The list page's WebPage JSON-LD is not the job -- ids come from hrefs."""
    net = vincere_net()
    feed = feed_for("zealhr", "vincere")

    record = await docxtract.harvest("vincere", feed, net)

    assert net.get_calls[0]["url"] == "https://zealhr.vincere.io/careers/"
    # one detail fetch per discovered link
    assert len(net.get_calls) == 3
    assert [item.external_id for item in record.items] == ["62146", "62113"]


async def test_vincere_parse_templates_posting_url_from_key_and_id():
    """The JobPosting block carries no `url`, so posting_url is synthesized."""
    net = vincere_net()
    feed = feed_for("zealhr", "vincere")
    record = await docxtract.harvest("vincere", feed, net)

    posting = docxtract.parse("vincere", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Creditors Clerk"
    assert posting.company_raw == "ZealHR"
    assert posting.url_posting == "https://zealhr.vincere.io/careers/job/62146"
    assert posting.location_raw == "City of Johannesburg Metropolitan Municipality, Gauteng, ZA"
    assert posting.employment_type == "FULL_TIME"
    # both detail items reuse the same fixture body -- external_id still comes
    # from the discovered link, not from re-deriving an id off the payload
    assert record.items[1].external_id == "62113"


async def test_jazzhr_harvest_finds_details_after_rss_retirement():
    """/apply/jobs.rss is 410 Gone; discovery goes through /apply/jobs/ links."""
    net = jazzhr_net()
    feed = feed_for("dtexsystems", "jazzhr")

    record = await docxtract.harvest("jazzhr", feed, net)

    assert net.get_calls[0]["url"] == "https://dtexsystems.applytojob.com/apply/jobs/"
    assert [item.external_id for item in record.items] == ["1gZyLb4EQX", "A71AimXpuQ"]


async def test_jazzhr_parse_keeps_the_vendor_supplied_url():
    """Unlike Vincere, JazzHR's JobPosting carries a real `url` -- no template."""
    net = jazzhr_net()
    feed = feed_for("dtexsystems", "jazzhr")
    record = await docxtract.harvest("jazzhr", feed, net)

    posting = docxtract.parse("jazzhr", feed, record.items[0], record.envelope)[0]

    assert posting.title == "Director, Product Engineering (AI POD Lead)"
    assert posting.company_raw == "DTEX Systems"
    assert posting.url_posting == (
        "https://dtexsystems.applytojob.com/apply/1gZyLb4EQX/"
        "Director-Product-Engineering-AI-POD-Lead"
    )
    assert posting.employment_type == "FULL_TIME"


async def test_vincere_and_jazzhr_are_registered_with_a_parser():
    for name in ("vincere", "jazzhr"):
        assert name in docxtract.CONFIGS
        assert callable(docxtract.harvester(name))
        assert callable(docxtract.parser(name))
