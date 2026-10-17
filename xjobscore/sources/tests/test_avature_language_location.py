"""Avature: English links whatever the session cookie says, and every
location markup tenants actually use (census of 2026-10-10)."""

from __future__ import annotations

import pytest

from xjobscore.net import Response
from xjobscore.records import Feed, Item
from xjobscore.sources import avature

HOST = "https://demo.avature.net"
JOB = "/careers/JobDetail/Finance-Intern/23684"


def response(url: str, body: str) -> Response:
    return Response(url=url, status=200, headers={}, body=body.encode())


def menu(path: str, locales: tuple[str, ...]) -> str:
    return "".join(f'<a href="{HOST}/{locale}{path}">{locale}</a>' for locale in locales)


def parse(page: str, key: str = "demo"):
    return avature.parse(Feed(1, "avature", key), Item("23684", page, kind="html"), {})[0]


class RouteNet:
    def __init__(self, routes: dict[str, Response]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url, *, params=None, headers=None, etag=None, concurrency=1):
        self.calls.append(url)
        return self.routes[url]


def test_english_url_swaps_the_locale_using_the_page_menu():
    links = [f"{HOST}/da_DK{JOB}", f"{HOST}/en_GB{JOB}", f"{HOST}/fr_FR{JOB}"]
    assert avature._english_url(f"{HOST}/es_ES{JOB}", links) == f"{HOST}/en_GB{JOB}"


def test_english_url_prefers_en_us():
    links = [f"{HOST}/en_GB{JOB}", f"{HOST}/en_US{JOB}"]
    assert avature._english_url(f"{HOST}/zh_CN{JOB}", links) == f"{HOST}/en_US{JOB}"


@pytest.mark.parametrize("url", [f"{HOST}/en_GB{JOB}", f"{HOST}{JOB}"])
def test_english_url_leaves_english_and_locale_less_urls(url):
    assert avature._english_url(url, [f"{HOST}/en_US{JOB}"]) is None


def test_english_url_needs_the_same_page_on_the_same_host():
    links = [f"{HOST}/en_US/careers/SearchJobs", f"https://other.avature.net/en_US{JOB}"]
    assert avature._english_url(f"{HOST}/es_ES{JOB}", links) is None


def test_url_is_english_even_when_the_first_job_link_is_the_language_menu():
    # The live Lisboa page: no canonical, og:url in Spanish, Danish menu first.
    page = (
        f'<meta property="og:url" content="{HOST}/es_ES{JOB}">'
        + menu(JOB, ("da_DK", "de_DE", "en_GB", "es_ES"))
    )
    assert parse(page).url_posting == f"{HOST}/en_GB{JOB}"


def test_canonical_already_in_english_is_kept():
    page = f'<link rel="canonical" href="{HOST}/en_GB{JOB}">' + menu(JOB, ("en_US",))
    assert parse(page).url_posting == f"{HOST}/en_GB{JOB}"


def test_a_board_without_english_keeps_its_own_url():
    page = f'<link rel="canonical" href="{HOST}/ja_JP{JOB}">' + menu(JOB, ("ja_JP",))
    assert parse(page).url_posting == f"{HOST}/ja_JP{JOB}"


async def test_harvest_rereads_a_non_english_board_in_english():
    swedish = f"{HOST}/sv_SE/careers/SearchJobs"
    english = f"{HOST}/en_US/careers/SearchJobs"
    detail = f"{HOST}/en_US{JOB}"
    net = RouteNet({
        f"{HOST}/careers/SearchJobs": response(swedish, menu("/careers/SearchJobs", ("en_US", "sv_SE"))),
        english: response(english, f'<a href="{detail}">job</a>'),
        detail: response(detail, "<p>job</p>"),
    })

    record = await avature.harvest(Feed(1, "avature", "demo"), net)

    assert net.calls == [f"{HOST}/careers/SearchJobs", english, detail]
    assert [item.external_id for item in record.items] == ["23684"]


async def test_harvest_reads_an_english_board_once():
    root = f"{HOST}/careers/SearchJobs"
    english = f"{HOST}/en_US/careers/SearchJobs"
    net = RouteNet({root: response(english, menu("/careers/SearchJobs", ("en_US", "fr_FR")))})

    await avature.harvest(Feed(1, "avature", "demo"), net)

    assert net.calls == [root]


def field(label: str, value: str) -> str:
    return (
        '<div class="article__content__view__field">'
        f'<div class="article__content__view__field__label">{label}</div>'
        f'<div class="article__content__view__field__value">{value}</div></div>'
    )


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        # Synopsys: separate City / State/Province fields.
        (field("City", "Sunnyvale") + field("State/Province", "California"), "Sunnyvale, California"),
        # Deloitte CE: City + Country.
        (field("City", "Plzen") + field("Country", "Czech Republic"), "Plzen, Czech Republic"),
        # TotalEnergies: definition lists, Portuguese labels.
        (
            '<dl class="article__content__view__field"><dt class="article__content__view__field__label"> País </dt>'
            '<dd class="article__content__view__field__value"> França </dd></dl>'
            '<dl class="article__content__view__field"><dt class="article__content__view__field__label"> Cidade </dt>'
            '<dd class="article__content__view__field__value"> INGRANDES </dd></dl>',
            "INGRANDES, França",
        ),
        # lululemon: data-map spans; a country "Location" plus finer fields.
        (
            '<span data-map="item-title"><strong>Location:</strong></span>'
            '<span data-map="item-value">United States of America</span>'
            '<span data-map="item-title"><strong>State/Province/City:</strong></span>'
            '<span data-map="item-value">Massachusetts</span>'
            '<span data-map="item-title"><strong>City:</strong></span>'
            '<span data-map="item-value">Millbury</span>',
            "Millbury, Massachusetts, United States of America",
        ),
        # NVA: one data-map City.
        (
            '<span data-map="item-title"><strong>City:</strong></span>'
            '<span data-map="item-value">Remote - USA</span>',
            "Remote - USA",
        ),
        # EA: an unlabeled locations block, with a per-part list after it.
        (
            '<div class="article__content__view__field field--locations">'
            '<div class="article__content__view__field__value"><strong>Locations</strong>: '
            'Austin, Texas, United States of America&nbsp;<ul class="MultipleDataSetFields">'
            '<li><span>Location:</span> <span>Austin</span></li></ul></div></div>',
            "Austin, Texas, United States of America",
        ),
        # Deloitte US: the header's location line.
        (
            '<div class="article__header--locations " > Washington, District of Columbia, United States </div>',
            "Washington, District of Columbia, United States",
        ),
        # JSON-LD only.
        (
            '<script type="application/ld+json">{"@type": "JobPosting", "jobLocation": '
            '{"address": {"addressLocality": "Boston", "addressRegion": "MA", "addressCountry": "US"}}}</script>',
            "Boston, MA, US",
        ),
    ],
)
def test_location_markups(page, expected):
    assert parse(page).location_raw == expected


def test_location_type_is_not_a_location():
    page = (
        '<span data-map="item-title">Location Type:</span><span data-map="item-value">Onsite</span>'
        '<span data-map="item-title">Department:</span><span data-map="item-value">Finance</span>'
    )
    assert parse(page).location_raw is None


def test_a_location_the_old_rule_found_is_untouched():
    page = field("Work Location", "Seattle, WA") + field("City", "Austin")
    assert parse(page).location_raw == "Seattle, WA"


def test_no_location_anywhere_stays_none():
    assert parse(field("Department", "Finance")).location_raw is None
