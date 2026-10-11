"""`company.domain` is a bare lowercase host, and never a job board's."""

from __future__ import annotations

import pytest

from xjobscore.store import normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme.com", "acme.com"),
        ("https://www.Acme.com/careers?x=1#top", "acme.com"),
        ("http://jobs.acme.co.uk:8443/path", "jobs.acme.co.uk"),
        ("  ACME.COM.  ", "acme.com"),
        ("https://user@acme.com", "acme.com"),
        ("", None),
        (None, None),
        ("localhost", None),
        ("not a domain.com", None),
        ("mailto:jobs", None),
    ],
)
def test_shapes_a_url_or_host_into_stored_form(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://boards.greenhouse.io/acme/jobs/1",
        "jobs.lever.co",
        "acme.wd5.myworkdayjobs.com",
        "https://www.rippling.com/careers/open-roles",
        "greenhouse.io",
    ],
)
def test_refuses_job_board_hosts_from_a_feed(raw):
    assert normalize_domain(raw) is None


def test_a_person_may_still_name_a_vendor_its_own_domain():
    assert normalize_domain("https://www.rippling.com/", board_hosts_ok=True) == "rippling.com"
