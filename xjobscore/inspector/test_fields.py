"""Path parsing, SQL compilation, and shaping for the Fields page.

The compiler is the only place in this package where a string from the
browser helps choose SQL, so most of what is asserted here is that it never
actually reaches the statement: keys become bind parameters, and only the
*count* of laterals -- which comes from the parse, not the text -- varies.
"""

from __future__ import annotations

import pytest

from xjobscore.inspector import fields


# ---- path parsing

def test_plain_path_is_one_group():
    assert fields.parse_path("title") == [["title"]]


def test_nested_path_stays_one_group():
    assert fields.parse_path("location.name") == [["location", "name"]]


def test_array_hop_splits_the_groups():
    assert fields.parse_path("departments[].name") == [["departments"], ["name"]]


def test_bare_array_reads_the_elements():
    assert fields.parse_path("offices[]") == [["offices"], []]


def test_two_array_hops():
    assert fields.parse_path("a[].b[].c") == [["a"], ["b"], ["c"]]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "a'; drop table raw_posting--",
        "../etc",
        "a[",
        "a]b",
        "a..b",
        "a.b.c.d.e.f.g.h.i.j.k.l.m.n",
    ],
)
def test_bad_paths_raise_value_error(path):
    with pytest.raises(ValueError):
        fields.parse_path(path)


# ---- SQL compilation

def test_scalar_path_compiles_to_a_leaf_with_no_laterals():
    params: list = ["greenhouse", None]
    laterals, leaf = fields._compile_path(fields.parse_path("location.name"), params)
    assert laterals == ""
    assert leaf == "latest.p #>> $3::text[]"
    assert params == ["greenhouse", None, ["location", "name"]]


def test_array_path_compiles_to_one_lateral():
    params: list = ["greenhouse", None]
    laterals, leaf = fields._compile_path(
        fields.parse_path("departments[].name"), params
    )
    assert laterals.count("jsonb_array_elements") == 1
    assert leaf == "e0.value #>> $4::text[]"
    assert params == ["greenhouse", None, ["departments"], ["name"]]


def test_two_array_hops_compile_to_two_laterals():
    params: list = ["x", None]
    laterals, _ = fields._compile_path(fields.parse_path("a[].b[].c"), params)
    assert laterals.count("jsonb_array_elements") == 2
    assert params[2:] == [["a"], ["b"], ["c"]]


def test_no_path_text_is_ever_interpolated_into_sql():
    """The keys travel as parameters; the SQL mentions ordinals only. A key
    that reaches the statement text would be the one real injection risk on
    this page."""
    params: list = ["greenhouse", None]
    laterals, leaf = fields._compile_path(
        fields.parse_path("weird_key[].other"), params
    )
    assert "weird_key" not in laterals + leaf
    assert "other" not in laterals + leaf


def test_lateral_is_a_left_join_so_empty_arrays_still_count():
    """A posting whose array is empty must remain in the histogram as an
    absent value; CROSS JOIN would silently drop it."""
    params: list = ["x", None]
    laterals, _ = fields._compile_path(fields.parse_path("a[]"), params)
    assert "LEFT JOIN LATERAL" in laterals
    assert "CROSS JOIN" not in laterals


def test_lateral_guards_the_argument_rather_than_filtering_after():
    """jsonb_array_elements raises on a non-array; a WHERE clause runs too
    late to prevent it, so the CASE inside the call is load bearing."""
    params: list = ["x", None]
    laterals, _ = fields._compile_path(fields.parse_path("a[]"), params)
    assert "CASE WHEN jsonb_typeof" in laterals
    assert "ELSE '[]'::jsonb END" in laterals


# ---- argument validation

def test_adapter_must_look_like_an_adapter():
    with pytest.raises(ValueError):
        fields._validate_adapter("green'house")
    assert fields._validate_adapter("greenhouse") == "greenhouse"


def test_feed_must_belong_to_the_adapter():
    assert fields._validate_feed("greenhouse:acme", "greenhouse") == "greenhouse:acme"
    assert fields._validate_feed("", "greenhouse") is None
    with pytest.raises(ValueError):
        fields._validate_feed("ashby:acme", "greenhouse")


def test_sample_must_be_one_of_the_offered_sizes():
    with pytest.raises(ValueError):
        fields._validate_sample(999)
    assert fields._validate_sample(2_000) == 2_000


def test_depth_is_bounded():
    with pytest.raises(ValueError):
        fields._validate_depth(0)
    with pytest.raises(ValueError):
        fields._validate_depth(fields.MAX_DEPTH + 1)


# ---- shaping

def test_survey_shaping_derives_fill_and_coverage():
    rows = fields._shape_survey(
        [
            {
                "path": "departments[].name",
                "occurrences": 400,
                "postings": 180,
                "filled": 380,
                "distinct_values": 42,
                "kinds": ["string"],
                "example": "Engineering",
            }
        ],
        sampled=200,
    )
    [row] = rows
    assert row["fill"] == pytest.approx(0.95)
    assert row["coverage"] == pytest.approx(0.9)
    assert row["leaf"] is True


def test_survey_shaping_marks_a_container_as_not_a_leaf():
    [row] = fields._shape_survey(
        [
            {
                "path": "departments",
                "occurrences": 200,
                "postings": 200,
                "filled": 200,
                "distinct_values": 90,
                "kinds": ["array"],
                "example": None,
            }
        ],
        sampled=200,
    )
    assert row["leaf"] is False


def test_survey_shaping_survives_an_empty_sample():
    [row] = fields._shape_survey(
        [
            {
                "path": "x",
                "occurrences": 0,
                "postings": 0,
                "filled": 0,
                "distinct_values": 0,
                "kinds": [],
                "example": None,
            }
        ],
        sampled=0,
    )
    assert row["fill"] == 0.0
    assert row["coverage"] == 0.0
