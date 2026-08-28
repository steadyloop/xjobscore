"""The display taxonomy stays glued to the adapter config tables."""

from __future__ import annotations

from xjobscore import sources
from xjobscore.inspector.profile import (
    ADAPTERS,
    ARCHETYPES,
    FORMATS,
    source_profile,
    wire_content_type,
)
from xjobscore.sources import docxtract, json_api


def test_bespoke_map_is_exactly_the_sources_outside_both_spec_tables():
    bespoke = set(sources.SOURCES) - set(json_api.SPECS) - set(docxtract.SPECS)

    assert bespoke == {
        "avature",
        "dayforce",
        "hn",
        "janestreet",
        "join",
        "taleo",
        "workatastartup",
        "zohorecruit",
    }


def test_every_registered_source_profiles_into_the_taxonomy():
    for source in sources.SOURCES:
        profile = source_profile(source)
        assert profile["adapter"] in ADAPTERS, source
        assert set(profile["formats"]) <= set(FORMATS), source
        assert set(profile["archetypes"]) <= set(ARCHETYPES), source
        assert profile["archetypes"], source


def test_known_source_shapes():
    assert source_profile("greenhouse") == {
        "adapter": "json-api", "formats": ["json"], "flavor": None, "archetypes": ["single"],
    }
    # "detail" is back as of 2026-08-26, but GATED -- the profile records the
    # shape, not the gate, so paginated+detail is what it reports.
    assert source_profile("workday")["archetypes"] == ["paginated", "detail"]
    assert source_profile("smartrecruiters")["archetypes"] == ["paginated", "detail"]
    assert source_profile("oracle")["archetypes"] == ["paginated", "batch-detail"]
    assert source_profile("jobvite") == {
        "adapter": "docxtract",
        "formats": ["html", "jsonld"],
        "flavor": "schemaorg",
        "archetypes": ["link-discovery", "detail"],
    }
    assert source_profile("teamtailor") == {
        "adapter": "docxtract", "formats": ["xml"], "flavor": "rss", "archetypes": ["single"],
    }
    assert source_profile("hn") == {
        "adapter": "bespoke",
        "formats": ["json"],
        "flavor": None,
        "archetypes": ["discovery", "paginated"],
    }


def test_unknown_source_falls_back_gracefully():
    assert source_profile("never-heard-of-it") == {
        "adapter": "bespoke", "formats": [], "flavor": None, "archetypes": [],
    }


def test_wire_content_type_strips_parameters_and_skips_errors():
    network = {
        "requests": [
            {"status": None, "error": {"class": "NetError"}},
            {"status": 200, "response_headers": {"content-type": "Application/JSON; charset=utf-8"}},
        ]
    }

    assert wire_content_type(network) == "application/json"
    assert wire_content_type({}) is None
    # A run whose only response is an error page names no payload format.
    assert wire_content_type(
        {"requests": [{"status": 404, "response_headers": {"content-type": "text/html"}}]}
    ) is None
