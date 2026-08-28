import json

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
