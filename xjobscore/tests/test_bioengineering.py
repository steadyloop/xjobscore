"""Focus chips for bioengineering students, pinned to real posting titles."""

from pathlib import Path

import pytest

from xjobscore import bioengineering
from xjobscore.bioengineering import EmployerRegistryError, classify

M = bioengineering.MEDTECH_FILTER
R = bioengineering.REGULATORY_FILTER
B = bioengineering.BIOTECH_FILTER


def chips(title, description="", *, company="", segments=None):
    return set(classify(title, description, company=company, segments=segments).chips)


# Strong title words need no employer -------------------------------------------


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Biomedical Engineering Intern", {M}),
        ("Neuroengineer Intern", {M}),
        ("Biomed Equipment Technician Apprentice", {M}),
        ("Mechanical Engineering Intern, Brain Interfaces", {M}),
        ("Student Placement: Medical Physics", {M}),
        ("Clinical Research Interns - Clinical Trials Office", {R}),
        ("Clinical Trials Project Management Internship", {R}),
        ("2027 Future Talent Program - Bioanalytical Sciences - Intern", {B}),
        ("Intern - Kite Research - Viral Vector Core", {B}),
        ("Design Engineering Co-Op (Biopharma)", {B}),
        ("Student Research Assistant - Biomedical Engineering", {M, B}),
    ],
)
def test_strong_titles_count_at_any_employer(title, expected):
    assert chips(title, company="Unregistered Co") == expected


# Generic titles count only at a registered life-science employer ------------------


@pytest.mark.parametrize(
    "title, company, expected",
    [
        ("Mechanical Engineering Intern", "Stryker", {M}),
        ("Co-op, Embedded Software Engineering: January-June 2027 (Onsite)", "insulet", {M}),
        ("Quality Engineering Intern", "Stryker", {M, R}),
        ("Spring Co-Op 2027, Process Development Upstream", "vrtx", {B}),
        ("Undergrad Intern - Operations – Process Development (Summer 2027)", "Amgen", {B}),
        ("Manufacturing Operator III, UD Fill - A1 Shift", "AbbVie", {B}),
        ("Process Engineer Intern - Life Sciences (Summer 2027)", "CRB", {B}),
    ],
)
def test_generic_titles_follow_the_employer(title, company, expected):
    assert chips(title, company=company) == expected
    assert chips(title, company="Ford Motor Company") == set()


def test_therapeutic_areas_do_not_make_a_clinical_role_biotech():
    assert chips("Intern - Development - Clinical Operations, Oncology", company="gilead") == {R}
    assert chips("2027 Future Talent Program Neuroscience Discovery Co-op", company="MSD") == {B}


def test_validation_consultancies_place_engineers_at_biopharma_sites():
    assert chips("CQV Engineer (New Grad)", company="Verista, Inc.") == {R, B}
    assert chips("CQV Engineer (New Grad)", company="Ford Motor Company") == {R}


def test_program_wide_postings_take_the_employer_chip():
    assert chips("Undergrad Intern", company="Amgen") == {B}
    assert chips("2027 Spring Co-Op Opportunities, Framingham, MA", company="sanofi") == {B}
    assert chips("Debut Internship Talent Pool", company="Debut Biotech") == {B}
    assert chips("Intern", company="Morgan Stanley") == set()


def test_device_and_pharma_employers_route_by_role():
    assert chips("Mechanical Engineering Intern - Robotics R&D", company="jj") == {M}
    assert chips("IMM Discovery Intern Spring House", company="jj") == {B}
    assert chips("Oncology Discovery Scientist Intern", company="jj") == {B}


# Vetoes ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, company",
    [
        ("Summer 2027 Internship - Joint Replacement Sales - Chicago, IL", "Stryker"),
        ("Pharmaceutical Sales Intern", "pfizer"),
        ("Finance Intern, Immunology", "gilead"),
        ("Product Marketing Intern - Neuroscience", "MSD"),
        ("Biotechnology Partnerships Associate", "Amgen"),
        ("Videographer Internship - Medical Devices", "Medtronic"),
        ("IT Intern - Summer 2027", "Medtronic"),
        ("2027 Business Technology Solutions Intern - Cloud Engineering (Undergraduate)", "AbbVie"),
        ("Undergrad Intern – Machine Learning Engineer – Technology, AI & Data (Summer 2027)", "Amgen"),
        ("Intern, Internal Controls: June- August (Hybrid)", "insulet"),
        ("Patient Care Technician - PCT", "freseniusmedicalcare"),
        ("Ultrasound Student Worker - PRN", "Some Health System"),
        ("Clinical Practicum Intern (Master's Level)", "Charlie Health"),
        ("Intern - Medical Technologist (Flow Cytometry)", "labcorp"),
        ("Biology - Graduates - AI Training - Seattle, US", "Prolific"),
        ("Part-Time Lecturer, Science and Business Courses for Biotechnology", "northeastern"),
    ],
)
def test_business_and_patient_care_roles_never_earn_a_chip(title, company):
    assert chips(title, company=company) == set()


@pytest.mark.parametrize(
    "title, company, expected",
    [
        ("Post Market Quality Engineering Intern", "Stryker", {M, R}),
        ("Summer 2027 Internship - RA/QA - Post Market Regulatory Affairs - Virtual", "Stryker", {M, R}),
        ("Intern - Development - Global Regulatory Policy & Intelligence, Regulatory Affairs", "gilead", {R}),
        ("Drug Delivery Intern", "Unregistered Co", {B}),
        ("Adverse Event Reporting Intern", "pfizer", {R}),
    ],
)
def test_life_science_phrases_survive_their_veto_words(title, company, expected):
    assert chips(title, company=company) == expected


# Collisions with other industries ------------------------------------------------


def test_semiconductor_implant_is_not_a_medical_implant():
    assert chips("Ion Implant Process Engineer Intern", company="Applied Materials") == set()
    assert chips("Electrical Engineer Intern, Implant Embedded Systems", company="Neuralink") == {M}


def test_regulatory_titles_outside_life_sciences_need_a_life_science_regulator():
    utility = "We serve 3.3 million natural gas customers and file rate cases."
    assert chips("Regulatory Affairs Intern", utility, company="nisource") == set()
    device = "Support 510(k) submissions to the FDA for our catheter platform."
    assert chips("Regulatory Affairs Intern", device, company="Unregistered Co") == {M, R}


def test_manufacturing_quality_titles_are_not_life_science_quality():
    assert chips("Intern, Quality Systems & Assurance", company="zekelman") == set()
    assert chips("Quality & Manufacturing Intern (Spring 2027)", company="Zipline") == set()


def test_food_and_water_labs_do_not_borrow_wet_lab_evidence():
    food = "Run PCR and ELISA pathogen screens for our dairy and cheese plants (HACCP)."
    assert chips("Microbiology Intern, Summer 2027", food, company="smucker") == set()
    water = "Operate the membrane bioreactor pilot at our wastewater plant."
    assert chips("Graduate Engineer - Membrane Treatment", water, company="Unregistered Co") == set()


def test_battery_cell_engineering_needs_bio_context():
    assert chips("Cell Engineering Intern", company="Tesla") == set()
    assert chips("Cell Engineering Intern", company="gilead") == {B}


def test_chemistry_only_roles_are_not_biotech():
    title = "2027 Future Talent Program - Discovery Chemistry & Process Research - Intern"
    assert chips(title, company="MSD") == set()


def test_field_clinical_roles_are_medtech_not_regulatory():
    assert chips("Associate Clinical Experience (ACE) Intern – Summer 2027", company="Medtronic") == {M}
    assert chips("CRM Clinical Field Intern - Summer 2027", company="Abbott") == {M}


def test_software_qa_is_not_regulatory_quality():
    title = "Firmware Quality Assurance Engineer Co-Op Intern Full Time January-June 2027"
    assert chips(title, company="irhythmtech") == {M}


# Description evidence stands in for an unregistered employer ---------------------


def test_role_level_device_terms_admit_a_generic_title():
    description = "Own design controls and ISO 13485 documentation for a Class II catheter."
    assert chips("Quality Engineering Intern", description, company="Unregistered Co") == {M, R}


def test_industry_words_in_boilerplate_do_not_admit_a_title():
    footer = "Wireless and Medical Devices: per the Department of Energy, personal devices..."
    assert chips("Laser Systems Engineering Intern", footer, company="Lawrence Livermore National Laboratory") == set()


def test_medical_school_research_assistants_are_biotech():
    assert chips("Student Research Assistant - SOM Urology", company="tulane-ibqejb.fa.ocs") == {B}
    assert chips("Student Research Assistant - Health Policy and Management", company="tulane-ibqejb.fa.ocs") == set()


# Registry ------------------------------------------------------------------------


def test_registry_matches_company_names_case_insensitively():
    assert bioengineering.employer_segments("STRYKER") == frozenset({"dev"})
    assert bioengineering.employer_segments("jj") == frozenset({"dev", "pharma"})
    assert bioengineering.employer_segments("Ford Motor Company") == frozenset()
    assert bioengineering.employer_segments(None) == frozenset()


def test_every_registry_segment_is_known():
    for segments in bioengineering.employers().values():
        assert segments <= bioengineering.SEGMENTS


def test_registry_rejects_unknown_segments(tmp_path: Path):
    path = tmp_path / "employers.yaml"
    path.write_text("segments:\n  devices:\n    - Stryker\n", encoding="utf-8")
    with pytest.raises(EmployerRegistryError, match="unknown segment"):
        bioengineering.employers(path)


def test_focus_version_tracks_the_registry():
    version = bioengineering.focus_version()
    assert version.startswith(f"{bioengineering.FOCUS_VERSION}.")
    assert len(version.split(".")[1]) == 12


def test_reasons_name_the_evidence_behind_each_chip():
    evidence = classify("Mechanical Engineering Intern", company="Stryker")
    assert evidence.chips == (M,)
    assert dict(evidence.reasons) == {M: "employer"}
    assert classify("Sales Intern", company="Stryker").veto == "business"
