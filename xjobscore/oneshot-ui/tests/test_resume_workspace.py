"""Pure workspace rules — no database, no event loop.

These pin the semantics that were expressed in SQL before the Postgres port and
are now Python: position allocation, global ordering, case-sensitive skill
uniqueness, and None-for-missing.
"""

from oneshot_ui import resume_workspace as workspace


def bank():
    return workspace.blank_workspace()


def test_position_is_allocated_per_kind_but_ordered_globally():
    state = bank()
    workspace.add_entry(state, {"kind": "project", "org": "P1"})
    workspace.add_entry(state, {"kind": "education", "org": "E1"})
    workspace.add_entry(state, {"kind": "project", "org": "P2"})
    workspace.add_entry(state, {"kind": "education", "org": "E2"})

    # P1/E1 are both position 1 and P2/E2 both position 2; ties break on id.
    # Sorting per kind and concatenating would give P1,P2,E1,E2 instead.
    assert [e["org"] for e in workspace.list_entries(state)] == ["P1", "E1", "P2", "E2"]


def test_bullet_position_is_scoped_to_its_entry():
    state = bank()
    first = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    second = workspace.add_entry(state, {"kind": "project", "org": "P2"})

    a = workspace.add_bullet(state, first["id"], {"text": "a"})
    b = workspace.add_bullet(state, first["id"], {"text": "b"})
    c = workspace.add_bullet(state, second["id"], {"text": "c"})

    assert (a["position"], b["position"]) == (1, 2)
    assert c["position"] == 1, "a new entry's bullets start at 1 again"


def test_bullets_carry_a_derived_entry_id():
    state = bank()
    entry = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    bullet = workspace.add_bullet(state, entry["id"], {"text": "a"})

    assert bullet["entry_id"] == entry["id"]
    listed = workspace.list_entries(state)[0]["bullets"][0]
    assert listed["entry_id"] == entry["id"]


def test_ids_come_from_one_counter_shared_by_entries_bullets_and_skills():
    state = bank()
    entry = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    bullet = workspace.add_bullet(state, entry["id"], {"text": "a"})
    skill = workspace.add_skill(state, {"category": "language", "name": "Python"})

    assert [entry["id"], bullet["id"], skill["id"]] == [1, 2, 3]
    assert state["next_item_id"] == 4


def test_duplicate_skill_is_case_sensitive():
    state = bank()
    assert workspace.add_skill(state, {"category": "language", "name": "Python"})
    # Exact match is a duplicate (SQLite's UNIQUE under BINARY collation).
    assert workspace.add_skill(state, {"category": "language", "name": "Python"}) is None
    # Different case is a different skill, and a different category is too.
    assert workspace.add_skill(state, {"category": "language", "name": "python"})
    assert workspace.add_skill(state, {"category": "tool", "name": "Python"})


def test_skill_edit_keeps_its_id_and_refuses_a_duplicate():
    state = bank()
    python = workspace.add_skill(state, {"category": "language", "name": "Python"})
    workspace.add_skill(state, {"category": "language", "name": "Go"})

    changed = workspace.edit_skill(state, python["id"], {"name": "Python 3"})
    assert changed["id"] == python["id"]
    assert changed["name"] == "Python 3"

    try:
        workspace.edit_skill(state, python["id"], {"name": "Go"})
    except ValueError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("duplicate edit should fail")


def test_skills_sort_by_category_then_name_without_casefolding():
    state = bank()
    for category, name in [
        ("tool", "Docker"), ("language", "python"), ("language", "Python"), ("language", "C"),
    ]:
        workspace.add_skill(state, {"category": category, "name": name})

    assert [(s["category"], s["name"]) for s in workspace.list_skills(state)] == [
        ("language", "C"), ("language", "Python"), ("language", "python"), ("tool", "Docker"),
    ]


def test_edits_to_missing_rows_return_none_and_deletes_return_false():
    state = bank()
    assert workspace.edit_entry(state, 999, {"org": "x"}) is None
    assert workspace.edit_bullet(state, 999, {"text": "x"}) is None
    assert workspace.edit_skill(state, 999, {"name": "x"}) is None
    assert workspace.remove_entry(state, 999) is False
    assert workspace.remove_bullet(state, 999) is False
    assert workspace.remove_skill(state, 999) is False


def test_deleting_an_entry_takes_its_bullets_with_it():
    state = bank()
    entry = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    bullet = workspace.add_bullet(state, entry["id"], {"text": "a"})

    assert workspace.remove_entry(state, entry["id"]) is True
    assert workspace.edit_bullet(state, bullet["id"], {"text": "b"}) is None


def test_an_empty_update_leaves_the_row_alone():
    state = bank()
    entry = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    # None values are skipped, matching the old `v is not None` SQL filter.
    unchanged = workspace.edit_entry(state, entry["id"], {"org": None, "role": None})
    assert unchanged["org"] == "P1"


def test_returned_values_are_copies_not_references():
    state = bank()
    entry = workspace.add_entry(state, {"kind": "project", "org": "P1"})
    entry["org"] = "mutated by caller"
    assert workspace.list_entries(state)[0]["org"] == "P1"


def test_normalize_repairs_a_counter_that_would_reissue_a_live_id():
    # A hand-edited row, or a botched import, could leave next_item_id behind
    # the ids actually in use — which would silently merge two bullets.
    state = workspace.normalize({
        "profile": {},
        "entries": [{"id": 40, "kind": "project", "org": "P", "position": 1,
                     "bullets": [{"id": 77, "text": "a", "position": 1}]}],
        "skills": [{"id": 12, "category": "language", "name": "C"}],
        "next_item_id": 3,
    })
    assert state["next_item_id"] == 78
    assert workspace.add_skill(state, {"category": "tool", "name": "Git"})["id"] == 78


def test_normalize_fills_a_missing_profile_and_empty_collections():
    state = workspace.normalize(None)
    assert state["profile"]["full_name"] == ""
    assert state["profile"]["links"] == []
    assert state["entries"] == [] and state["skills"] == []
    assert state["next_item_id"] == 1


def test_coverage_splits_matched_from_gap_and_flags_unbanked_vocabulary():
    state = bank()
    python = workspace.add_skill(state, {"category": "language", "name": "Python"})
    workspace.add_skill(state, {"category": "language", "name": "Go"})
    job = {"id": 1, "jd_text": "We use Python and Go, plus Rust and Monte Carlo methods."}

    result = workspace.compute_coverage(job, workspace.list_skills(state), {python["id"]})

    assert [s["name"] for s in result["matched"]] == ["Python"]
    assert [s["name"] for s in result["have_not_shown"]] == ["Go"]
    assert "Rust" in result["missing"]
    assert "Monte Carlo" in result["missing"]
    # Banked skills are never reported as missing.
    assert "Python" not in result["missing"]


def test_coverage_matches_whole_words_only():
    state = bank()
    workspace.add_skill(state, {"category": "language", "name": "R"})
    job = {"id": 1, "jd_text": "Strong Rust and React experience required."}

    result = workspace.compute_coverage(job, workspace.list_skills(state), set())

    # "R" must not match inside "Rust" or "React".
    assert result["matched"] == [] and result["have_not_shown"] == []


def test_coverage_treats_plus_hash_and_ampersand_as_part_of_a_word():
    """"C" must not match the C in "C++" or "C#", nor "R" the R in "R&D"."""
    state = bank()
    for name in ("C", "R", "C++", "C#"):
        workspace.add_skill(state, {"category": "language", "name": name})
    skills = workspace.list_skills(state)
    jd = "Experience with C++, C#, and an R&D background."

    result = workspace.compute_coverage({"id": 1, "jd_text": jd}, skills, set())

    assert sorted(s["name"] for s in result["have_not_shown"]) == ["C#", "C++"]


def test_coverage_still_matches_a_name_that_ends_a_sentence():
    """`.` stays a boundary: excluding it would lose "Backend in Go." entirely."""
    state = bank()
    workspace.add_skill(state, {"category": "language", "name": "Go"})

    result = workspace.compute_coverage(
        {"id": 1, "jd_text": "Backend in Go."}, workspace.list_skills(state), set()
    )

    assert [s["name"] for s in result["have_not_shown"]] == ["Go"]
