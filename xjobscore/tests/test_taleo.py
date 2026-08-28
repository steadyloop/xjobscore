"""Taleo detail-page parsing: what is a posting, and what is merely a page."""

from __future__ import annotations

import pytest

from xjobscore.records import Feed, Item, Unparseable
from xjobscore.sources import taleo


FEED = Feed(
    id=1,
    source="taleo",
    key="kp",
    company_name="Example Health",
    type="ATS",
    config={"url": "https://example.taleo.net/careersection/ex/moresearch.ftl?lang=en"},
)

# The shape a real jobdetail.ftl carries: an inline schema declaration followed
# by the fillList call holding one requisition's URL-encoded values.
DEFINITION = (
    "descRequisition: {_size: 5, "
    "_hles: ['f0','f1','f2','f3','f4'], "
    "_hlid: ['title','description','primarylocation','contestnumber','postingdate']}"
)
FILL = (
    "api.fillList('descRequisitionInterface', 'descRequisition', "
    "['!*!Bioprocess%20Engineer','!*!%3Cp%3EBuild%20assays%3C%2Fp%3E',"
    "'!*!Boston%2C%20MA','!*!REQ-42','!*!01/05/2026']);"
)
DETAIL = f"<html><script>{DEFINITION};\n{FILL}</script></html>"

# What Taleo actually returns for a pulled requisition or an expired session: a
# 200 carrying the career section shell, with no requisition block at all.
LANDING = "<html><body><div id='requisitionListInterface'>Search jobs</div></body></html>"


def _parse(payload: str):
    return taleo.parse(FEED, Item(external_id="42", payload=payload, kind="html"), {})


def test_detail_page_parses_into_one_posting():
    posting, = _parse(DETAIL)

    assert posting.title == "Bioprocess Engineer"
    assert posting.location_raw == "Boston, MA"
    assert posting.requisition_id == "REQ-42"
    assert posting.url_posting == (
        "https://example.taleo.net/careersection/ex/jobdetail.ftl?job=42&lang=en"
    )


def test_page_without_a_requisition_block_is_unparseable_not_a_failure():
    """A pulled requisition and an expired session both answer 200 with this.

    It is the origin's answer about one posting, so it must reach silver as
    `Unparseable` -- the batch keeps every other posting beside it.
    """
    with pytest.raises(Unparseable):
        _parse(LANDING)


def test_definition_without_its_fill_call_is_unparseable():
    with pytest.raises(Unparseable):
        _parse(f"<html><script>{DEFINITION};</script></html>")


def test_malformed_requisition_block_still_fails_the_batch():
    """Shape drift is not absence: the schema is there and no longer fits."""
    fill = FILL.replace("'!*!01/05/2026'", "'!*!01/05/2026','!*!extra'")
    with pytest.raises(ValueError) as caught:
        _parse(f"<html><script>{DEFINITION};\n{fill}</script></html>")

    assert not isinstance(caught.value, Unparseable)


def test_listing_pages_keep_failing_the_harvest():
    """`harvest` has no per-item channel -- a listing with no requisition list
    is a broken fetch, and `Unparseable` subclasses ValueError so that the
    fetch leg's behaviour is unchanged."""
    with pytest.raises(ValueError):
        taleo._definition(LANDING, "listRequisition")
