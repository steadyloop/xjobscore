"""Config introspection: what an adapter reads, and what it collapses to.

No database and no fixtures -- these assert against the real config tables
and the real canons, so a config edit that changes an adapter's reads shows
up here rather than silently on the page.
"""

from __future__ import annotations

from xjobscore import silver
from xjobscore.inspector import mapping


def test_plain_path_expression():
    assert mapping._paths("@title") == ("title",)


def test_context_and_literal_expressions_read_no_payload():
    # "$key" is the harvest context, not the payload; a bare string is a
    # literal. Reporting either as a payload path would invent a field.
    assert mapping._paths("$key") == ()
    assert mapping._paths("exact") == ()


def test_operator_wrapping_a_path_is_transparent():
    assert mapping._paths({"unescape": "@content"}) == ("content",)
    assert mapping._paths({"date": "@first_published"}) == ("first_published",)


def test_fallback_reports_every_branch():
    assert mapping._paths({"fallback": ["@descriptionBody", "@description"]}) == (
        "descriptionBody",
        "description",
    )


def test_map_composes_into_survey_array_notation():
    """The inner expression sees a row, not the payload, so the path must
    nest -- otherwise "@name" would be reported as a top-level field."""
    assert mapping._paths({"map": "@offices", "value": "@name"}) == ("offices[].name",)


def test_map_without_a_value_reads_the_elements():
    assert mapping._paths({"map": "@secondaryLocations"}) == ("secondaryLocations[]",)


def test_first_reads_the_arrays_contents():
    assert mapping._paths({"first": "@locations"}) == ("locations[]",)


def test_template_tokens_are_payload_paths():
    paths = mapping._paths({"template": "https://x/{id}?src={$key}"})
    assert paths == ("id",)


def test_json_api_adapter_map_matches_the_config():
    result = mapping.adapter_map("greenhouse")
    assert result["engine"] == "json-api"
    assert result["introspectable"] is True
    # One raw path can feed several canonical fields: greenhouse derives
    # job_type from the title the `title` field also reads.
    assert result["paths"]["title"] == ["title", "job_type"]
    assert result["paths"]["absolute_url"] == ["url_posting", "apply_url"]
    assert result["paths"]["location.name"] == [
        "location_raw",
        "remote",
    ]
    assert result["paths"]["offices[].name"] == ["locations_extra"]
    # "extra" lands under source_extra, which is bronze-only downstream.
    assert result["paths"]["language"] == ["source_extra.language"]


def test_ashby_postal_address_only_feeds_location_normalization():
    result = mapping.adapter_map("ashby")

    assert result["paths"]["location"] == [
        "location_raw",
        "location_normalization_raw",
        "remote",
    ]
    assert result["paths"]["address.postalAddress.addressLocality"] == [
        "location_normalization_raw"
    ]
    assert result["paths"]["address.postalAddress.addressCountry"] == [
        "location_normalization_raw"
    ]
    assert result["paths"]["secondaryLocations[].location"] == [
        "location_normalization_extra",
        "locations_extra",
        "remote",
    ]
    assert result["paths"][
        "secondaryLocations[].address.postalAddress.addressLocality"
    ] == ["location_normalization_extra"]
    assert result["paths"][
        "secondaryLocations[].address.postalAddress.addressCountry"
    ] == ["location_normalization_extra"]


def test_a_selected_bag_entry_reports_both_halves_of_the_pair():
    """`{"first": {"map": "@metadata", "where": ..., "value": ...}}` reads the
    name it matches on as well as the value it returns. Reporting only the
    value would hide which entry the rule actually selects, and `first` must
    not append a second "[]" to paths `map` already made element-level."""
    result = mapping.adapter_map("greenhouse")
    assert result["paths"]["metadata[].name"] == ["job_type", "remote"]
    assert result["paths"]["metadata[].value"] == ["job_type", "remote"]
    assert "metadata[].value[]" not in result["paths"]


def test_docxtract_adapter_map_uses_element_paths():
    result = mapping.adapter_map("trakstar")
    assert result["engine"] == "docxtract"
    assert result["paths"]["locationCity"] == ["location_raw"]
    assert result["paths"]["team"] == ["source_extra.team"]
    assert "rss" in (result["note"] or "")


def test_bespoke_parser_says_so_instead_of_reporting_nothing():
    """An empty table and an un-introspectable parser are different answers,
    and conflating them would read as "taleo maps no fields"."""
    result = mapping.adapter_map("taleo")
    assert result["introspectable"] is False
    assert result["engine"] == "bespoke"
    assert result["paths"] == {}
    assert "taleo.py" in result["note"]


def test_unknown_adapter_is_named_as_unregistered():
    result = mapping.adapter_map("nosuchsource")
    assert result["engine"] == "unknown"
    assert result["introspectable"] is False


def test_annotate_marks_an_inherited_ancestor():
    """greenhouse captures the whole metadata array; an element's value_type is
    carried inside it without being read by name."""
    found = mapping.annotate(["title", "metadata[].value_type"], "greenhouse")
    assert found["title"] == {"fields": ["title", "job_type"], "inherited": None}
    assert found["metadata[].value_type"] == {
        "fields": ["source_extra.metadata"],
        "inherited": "metadata",
    }


def test_a_path_read_by_name_is_not_reported_as_inherited():
    """departments[].name used to be inherited-only. The job_type rules read it
    directly, so the direct answer replaces the ancestor rather than joining
    it -- otherwise the page would keep claiming nothing reads the element."""
    found = mapping.annotate(["departments[].name"], "greenhouse")
    assert found["departments[].name"] == {"fields": ["job_type"], "inherited": None}


def test_annotate_reports_an_unmapped_path_as_empty():
    assert mapping.annotate(["ai_disclaimer"], "greenhouse")["ai_disclaimer"] == {
        "fields": [],
        "inherited": None,
    }


def test_chain_for_a_projected_field():
    chain = mapping.chain("workplace_type_raw")
    assert chain["projected"] is True
    assert chain["target"] == "attributes.remote"
    assert chain["domain"] == ["yes", "hybrid"]


def test_chain_for_a_bronze_only_field():
    chain = mapping.chain("source_extra.department")
    assert chain["projected"] is False
    assert "never projected" in chain["note"]


def test_locations_extra_is_bronze_only():
    """Captured by many adapters, projected by none -- the single most useful
    thing this page reports, so it is pinned here."""
    assert mapping.chain("locations_extra")["projected"] is False


def test_domains_come_from_the_live_canons():
    domains = mapping.domains()
    # job_type is a parse-time column now; NULL is a real member of its domain,
    # because that is what a vendor with no measured signal emits.
    assert "intern" in domains["job_type"]
    assert None in domains["job_type"]
    assert domains["remote"] == ["yes", "hybrid", "unknown"]
    # The legacy silver attribute, still live for un-migrated vendors.
    assert domains["attributes.remote"] == ["yes", "hybrid"]
    assert "computer_science" in domains["attributes.degree_fields"]


def test_preview_reproduces_the_real_collapse():
    found = mapping.preview("workplace_type_raw", ["Remote", "Hybrid", "OnSite", None])
    assert found == {"Remote": "yes", "Hybrid": "hybrid", "OnSite": None}
    # Same function the pipeline runs, not a transcription of it.
    assert found["OnSite"] == silver._remote("OnSite")


def test_preview_is_absent_for_fields_with_no_pure_collapse():
    """description_html narrows via skills/degree extraction over the whole
    body; previewing it per distinct value would be a lie."""
    assert mapping.preview("description_html", ["anything"]) == {}
