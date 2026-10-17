"""Opt-in employer routing for shared boards; transport and other fields stay native."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from xjobscore.records import Feed, Item
from xjobscore.sources import json_api

FIXTURES = Path(__file__).parents[3] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def bamboo_posting(config, department="18531", external_id="52"):
    payload = fixture("bamboohr_detail")["result"]["jobOpening"]
    payload["departmentId"] = department
    feed = Feed(id=1, source="bamboohr", key="shared-board", config=config)
    return json_api.parse("bamboohr", feed, Item(external_id=external_id, payload=payload), {})[0]


@pytest.mark.parametrize("department, expected", [
    ("18531", "Painting Company"),
    ("18530", "Chimney Company"),
    ("99999", "Unresolved shared-board employers"),
])
def test_department_mapping_is_opt_in_and_preserves_native_posting(department, expected):
    spec = {"fallback": [
        {"lookup": [{"18531": "Painting Company", "18530": "Chimney Company"}, "@departmentId"]},
        "Unresolved shared-board employers",
    ]}
    original = bamboo_posting({}, department)
    configured = bamboo_posting({"company_raw_spec": spec}, department)
    assert original.company_raw == "shared-board"
    assert configured.company_raw == expected
    old_fields, new_fields = asdict(original), asdict(configured)
    old_fields.pop("company_raw")
    new_fields.pop("company_raw")
    assert old_fields == new_fields


def test_exact_posting_override_distinguishes_employers_sharing_a_department():
    spec = {"fallback": [
        {"lookup": [{"139": "Mobile Home Park", "142": "Construction Company"}, "$external_id"]},
        "Unresolved shared-board employers",
    ]}
    assert bamboo_posting({"company_raw_spec": spec}, "18577", "139").company_raw == "Mobile Home Park"
    assert bamboo_posting({"company_raw_spec": spec}, "18577", "142").company_raw == "Construction Company"
    assert bamboo_posting({"company_raw_spec": spec}, "18577", "143").company_raw == "Unresolved shared-board employers"


def test_literal_override_preserves_shared_board_transport_identity():
    posting = bamboo_posting({"company_raw_spec": "Actual Employer"})
    assert posting.company_raw == "Actual Employer"
    assert posting.external_id == "52"
    assert posting.url_posting == fixture("bamboohr_detail")["result"]["jobOpening"]["jobOpeningShareUrl"]


@pytest.mark.parametrize("with_employer", [True, False])
def test_workday_shared_board_can_use_verified_hiring_organization(with_employer):
    payload = fixture("workday_detail")
    payload["externalPath"] = "/job/verified-job"
    payload["hiringOrganization"] = {"name": "Franchise Operator"} if with_employer else {}
    item = Item(external_id="/job/verified-job", payload=payload)
    feed = Feed(id=1, source="workday", key="tenant/wd3/shared", config={})
    native = json_api.parse("workday", feed, item, {})[0]
    assert native.company_raw is None
    feed.config["company_raw_spec"] = {"fallback": [
        {"lookup": [{"Franchise Operator": "Verified Franchise Operator"}, "@hiringOrganization.name"]},
        "Unresolved shared-board employers",
    ]}
    configured = json_api.parse("workday", feed, item, {})[0]
    assert configured.company_raw == ("Verified Franchise Operator" if with_employer else "Unresolved shared-board employers")
    old_fields, new_fields = asdict(native), asdict(configured)
    old_fields.pop("company_raw")
    new_fields.pop("company_raw")
    assert old_fields == new_fields
    assert configured.url_posting == "https://tenant.wd3.myworkdayjobs.com/shared/job/verified-job"


def test_non_string_employer_override_is_rejected():
    with pytest.raises(ValueError, match="company_raw_spec"):
        bamboo_posting({"company_raw_spec": "@location"})
