import json

import pytest

from xjobscore.location_normalizer import _normalize_location, main, normalize_location


def test_normalize_location_is_importable_from_package() -> None:
    assert normalize_location("Santa Clara, California, United States") == {
        "locations": [
            {"country": "US", "state": "CA", "city": "Santa Clara"}
        ],
        "remote": False,
    }


def test_normalize_location_preserves_remote_signal() -> None:
    assert normalize_location("Remote, United States") == {
        "locations": [{"country": "US", "state": None, "city": None}],
        "remote": True,
    }


def test_formal_united_states_name_cannot_become_argentinian_america() -> None:
    assert normalize_location("Remote, US ; United States of America") == {
        "locations": [{"country": "US", "state": None, "city": None}],
        "remote": True,
    }


def test_bay_area_is_a_fallback_for_an_unresolved_metro() -> None:
    assert normalize_location(
        "Bay Area, CA, United States of America ; "
        "US - WA - Seattle Metro - Remote"
    ) == {
        "locations": [
            {"country": "US", "state": "WA", "city": "Seattle"},
            {"country": "US", "state": "CA", "city": "San Francisco"},
        ],
        "remote": True,
    }


def test_bay_area_never_displaces_a_more_specific_city() -> None:
    cases = {
        "Bay Area, Oakland, United States": "Oakland",
        "SF Bay Area, Burlingame, USA": "Burlingame",
        "Bay Area Office, Sunnyvale, United States": "Sunnyvale",
        "South Bay Area / Campbell, CA": "Campbell",
    }
    for value, city in cases.items():
        assert normalize_location(value)["locations"] == [
            {"country": "US", "state": "CA", "city": city}
        ]


def test_bay_area_and_peer_city_are_both_retained() -> None:
    assert normalize_location("Bay Area or Los Angeles Area")["locations"] == [
        {"country": "US", "state": "CA", "city": "Los Angeles"},
        {"country": "US", "state": "CA", "city": "San Francisco"},
    ]


def test_exact_san_mateo_component_is_appended_without_deleting_old_result() -> None:
    assert normalize_location("San Mateo, San Mateo") == {
        "locations": [
            {"country": "PH", "state": "40", "city": "San Mateo"},
            {"country": "US", "state": "CA", "city": "San Mateo"},
        ],
        "remote": False,
    }


def test_exact_new_jersey_component_is_appended_without_deleting_old_result() -> None:
    assert normalize_location("New Jersey ; New Jersey") == {
        "locations": [
            {"country": "JE", "state": None, "city": None},
            {"country": "US", "state": "NJ", "city": None},
        ],
        "remote": False,
    }


def test_explicit_us_is_retained_alongside_an_unrelated_selected_city() -> None:
    assert normalize_location("United States ; London, United Kingdom") == {
        "locations": [
            {"country": "GB", "state": "ENG", "city": "London"},
            {"country": "US", "state": None, "city": None},
        ],
        "remote": False,
    }


def test_component_reconciliation_does_not_treat_louisiana_as_los_angeles() -> None:
    assert normalize_location("Franklin, LA, Franklin, United States") == {
        "locations": [
            {"country": "US", "state": "LA", "city": "Franklin"},
        ],
        "remote": False,
    }


def test_us_abbreviation_requires_an_identifier_boundary() -> None:
    assert not any(
        location["country"] == "US"
        for location in normalize_location("Job Posting Only: USA1")["locations"]
    )


def test_normalize_location_reuses_work_without_sharing_mutable_results() -> None:
    _normalize_location.cache_clear()

    first = normalize_location("Santa Clara, California, United States")
    second = normalize_location("Santa Clara, California, United States")
    first["locations"][0]["city"] = "changed"

    assert _normalize_location.cache_info().misses == 1
    assert _normalize_location.cache_info().hits == 1
    assert second["locations"][0]["city"] == "Santa Clara"


def test_main_retains_jsonl_contract(tmp_path, capsys) -> None:
    source = tmp_path / "locations.jsonl"
    source.write_text(
        json.dumps({"location_text": "Toronto, Ontario, Canada"}) + "\n",
        encoding="utf-8",
    )

    main([str(source)])

    assert json.loads(capsys.readouterr().out) == {
        "input": "Toronto, Ontario, Canada",
        "locations": [{"country": "CA", "state": "ON", "city": "Toronto"}],
        "remote": False,
    }


def _states(value: str) -> list[tuple[str, str | None, str | None]]:
    return [(x["country"], x["state"], x["city"])
            for x in normalize_location(value)["locations"]]


def test_a_multi_word_state_beats_city_matches_on_its_own_halves() -> None:
    """`north` is a city in Utah and `carolina` one in Puerto Rico, so the city
    matcher used to consume both words and North Carolina never resolved. The
    state span strictly contains each city span, which is what settles it."""
    assert _states("Remote, North Carolina, USA") == [("US", "NC", None)]
    assert _states("North Dakota, Virtual Address") == [("US", "ND", None)]
    assert _states("New Hampshire") == [("US", "NH", None)]


def test_a_resolved_city_still_wins_its_own_state() -> None:
    """Suppression is scoped to spans the state strictly contains, so an equal
    span is still decided by the city/state compatibility check above it."""
    assert _states("Charlotte, North Carolina") == [("US", "NC", "Charlotte")]
    assert _states("New York, NY") == [("US", "NY", "New York City")]
    assert _states("Kansas City, Kansas") == [("US", "KS", "Kansas City")]


def test_a_suppressed_state_is_appended_not_dropped() -> None:
    """Removing the spurious city is only half of it: the `if not rows` fallback
    promotes state hits only when nothing at all was selected, so a surviving
    city elsewhere in the string would otherwise take the state down with it.
    The state is appended monotonically, never replacing an existing row."""
    assert _states("Atlanta; Florida; North Carolina; South Carolina") == [
        ("US", "GA", "Atlanta"), ("US", "NC", None), ("US", "SC", None),
    ]


@pytest.mark.parametrize(("value", "expected"), [
    ("Amsterdam, Noord-Holland, Nederland", ("NL", "07", "Amsterdam")),
    ("Amsterdam, Noord-Holland, Netherlands", ("NL", "07", "Amsterdam")),
    ("Amsterdam, Noord-Holland, NL", ("NL", "07", "Amsterdam")),
    ("AMSTERDAM, NOORD HOLLAND, Nederland", ("NL", "07", "Amsterdam")),
    ("Amsterdam, Noord Holland", ("NL", "07", "Amsterdam")),
    ("Rijssen, Overijssel, Nederland", ("NL", "15", "Rijssen")),
    ("Rijssen, Nederland", ("NL", "15", "Rijssen")),
    ("Rotterdam, Zuid-Holland, Nederland", ("NL", "11", "Rotterdam")),
    ("Eindhoven, Noord-Brabant, Nederland", ("NL", "06", "Eindhoven")),
    ("Utrecht, Utrecht, Nederland", ("NL", "09", "Utrecht")),
    # This locality is absent from the city dictionary. Its province/country
    # still supplies reliable foreign evidence instead of an invented US city.
    ("Koudekerk aan den Rijn, Zuid-Holland, Nederland", ("NL", "11", None)),
])
def test_dutch_country_and_provinces_do_not_invent_us_cities(value, expected):
    assert _states(value) == [expected]


@pytest.mark.parametrize(("value", "expected"), [
    ("Nederland", ("US", "TX", "Nederland")),
    ("Nederland, TX", ("US", "TX", "Nederland")),
    ("Nederland, Texas", ("US", "TX", "Nederland")),
    ("Nederland, TX, United States", ("US", "TX", "Nederland")),
    ("Nederland, TX, USA", ("US", "TX", "Nederland")),
    ("Nederland, CO", ("US", "CO", "Nederland")),
    ("Nederland, Colorado", ("US", "CO", "Nederland")),
    ("Nederland, CO, United States", ("US", "CO", "Nederland")),
    ("Holland, MI", ("US", "MI", "Holland")),
    ("South Holland, IL", ("US", "IL", "South Holland")),
    ("Amsterdam, NY", ("US", "NY", "Amsterdam")),
    ("Unknown town, Nederland", ("US", "TX", "Nederland")),
    ("NLD - Groningen", ("NL", "04", "Groningen")),
    ("Utrecht, Netherlands", ("NL", "09", "Utrecht")),
])
def test_existing_city_and_ambiguous_location_results_are_preserved(value, expected):
    assert _states(value) == [expected]


@pytest.mark.parametrize("separator", ["; ", " | ", " / "])
@pytest.mark.parametrize("dutch", [
    "Amsterdam, Noord-Holland, Nederland",
    "Rijssen, Overijssel, Nederland",
    "Koudekerk aan den Rijn, Zuid-Holland, Nederland",
])
@pytest.mark.parametrize(("us", "expected"), [
    ("Nederland, TX", ("US", "TX", "Nederland")),
    ("Nederland, CO", ("US", "CO", "Nederland")),
    ("Holland, MI", ("US", "MI", "Holland")),
    ("South Holland, IL", ("US", "IL", "South Holland")),
    ("Amsterdam, NY", ("US", "NY", "Amsterdam")),
    ("Austin, TX", ("US", "TX", "Austin")),
    ("Seattle, WA", ("US", "WA", "Seattle")),
    ("San Francisco, CA", ("US", "CA", "San Francisco")),
    ("San Mateo", ("US", "CA", "San Mateo")),
    ("New Jersey", ("US", "NJ", None)),
    ("Remote, United States", ("US", None, None)),
])
@pytest.mark.parametrize("us_first", [False, True])
def test_dutch_address_never_removes_a_separate_us_office(separator, dutch, us, expected, us_first):
    parts = [us, dutch] if us_first else [dutch, us]
    result = normalize_location(separator.join(parts))
    assert expected in [(p['country'], p['state'], p['city']) for p in result['locations']]
    assert result['remote'] == ('Remote' in us)


@pytest.mark.parametrize("value", [
    "Amsterdam, Noord-Holland, United States",
    "Amsterdam, Nederland, Austin, TX",
    "Amsterdam, Noord-Holland, Nederland, Austin, TX",
    "Nederland, Texas, Nederland",
])
def test_conflicting_us_evidence_keeps_the_posting_us_eligible(value):
    assert any(p['country'] == 'US' for p in normalize_location(value)['locations'])


@pytest.mark.parametrize(("us", "expected"), [
    ("Seattle", ("US", "WA", "Seattle")),
    ("San Francisco", ("US", "CA", "San Francisco")),
    ("Austin", ("US", "TX", "Austin")),
    ("Boston", ("US", "MA", "Boston")),
])
@pytest.mark.parametrize("dutch", [
    "Amsterdam, Noord-Holland, Nederland", "Rijssen, Overijssel, Nederland",
])
@pytest.mark.parametrize("separator", [", ", " or "])
@pytest.mark.parametrize("us_first", [True, False])
def test_city_only_us_office_survives_an_ambiguous_combined_address(us, expected, dutch, separator, us_first):
    parts = [us, dutch] if us_first else [dutch, us]
    assert expected in _states(separator.join(parts))
