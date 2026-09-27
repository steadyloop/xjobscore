"""Greenhouse auto-apply: URL gating, plan building, and the two routes.

The browser is never opened here; the API tests inject a fake client that
records what it was asked to submit.
"""

from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from oneshot_ui.app import create_app
from oneshot_ui.auth import session_account
from oneshot_ui.config import Settings
from oneshot_ui.greenhouse_apply import (
    HostedPosting,
    SubmissionResult,
    answer_key,
    bankable_answers,
    build_plan,
    missing_required,
    parse_hosted_url,
)

ACCOUNT = UUID("00000000-0000-0000-0000-0000000000aa")

PROFILE = {
    "full_name": "Chris Kim",
    "email": "chris@example.test",
    "phone": "+1 415-555-0100",
    "location": "San Diego, CA",
    "links": [
        {"url": "https://linkedin.com/in/chris", "label": "LinkedIn"},
        {"url": "https://github.com/chris", "label": "Github"},
    ],
}


def question(label, name, kind, required=True, values=()):
    return {
        "label": label,
        "required": required,
        "fields": [
            {"name": name, "type": kind, "values": [{"label": v, "value": i} for i, v in enumerate(values)]}
        ],
    }


JOB = {
    "id": 8191885,
    "title": "Intern - Database Conversion Programming",
    "company_name": "NISC",
    "questions": [
        question("First Name", "first_name", "input_text"),
        question("Last Name", "last_name", "input_text"),
        question("Preferred First Name", "preferred_name", "input_text", required=False),
        question("Email", "email", "input_text"),
        question("Phone", "phone", "input_text"),
        {
            "label": "Resume/CV",
            "required": True,
            "fields": [
                {"name": "resume", "type": "input_file", "values": []},
                {"name": "resume_text", "type": "textarea", "values": []},
            ],
        },
        {
            "label": "Cover Letter",
            "required": False,
            "fields": [
                {"name": "cover_letter", "type": "input_file", "values": []},
                {"name": "cover_letter_text", "type": "textarea", "values": []},
            ],
        },
        question("Your LinkedIn Profile URL", "question_1", "input_text", required=False),
        question("How did you hear about this job?", "question_2", "multi_value_single_select", values=("Handshake", "LinkedIn", "Other")),
        question("Why are you interested in this opportunity?", "question_3", "textarea"),
        question("What locations are you interested in?", "question_4[]", "multi_value_multi_select", values=("Atlanta, GA", "Remote")),
        question("Are you legally authorized to work in the United States?", "question_5", "multi_value_single_select", values=("Yes", "No")),
    ],
}


def by_name(plan):
    return {item.name: item for item in plan}


def test_only_greenhouse_hosted_apply_pages_are_supported():
    posting = parse_hosted_url("https://job-boards.greenhouse.io/testnisc/jobs/8191885?gh_src=abc")
    assert posting == HostedPosting("testnisc", 8191885, "https://job-boards.greenhouse.io/testnisc/jobs/8191885")
    assert posting.questions_url == "https://boards-api.greenhouse.io/v1/boards/testnisc/jobs/8191885?questions=true"
    assert parse_hosted_url("https://job-boards.eu.greenhouse.io/oet/jobs/4905215101").board == "oet"
    assert parse_hosted_url("https://boards.greenhouse.io/legacy/jobs/123/").job_id == 123
    assert parse_hosted_url("https://www.janestreet.com/join-jane-street/position/123/") is None
    assert parse_hosted_url("http://job-boards.greenhouse.io/x/jobs/1") is None
    assert parse_hosted_url("https://job-boards.greenhouse.io/x/jobs") is None
    assert parse_hosted_url(None) is None


def test_answer_key_folds_case_punctuation_and_spacing():
    assert answer_key("How did you hear about this job?") == "how did you hear about this job"
    assert answer_key("  Are you  legally-authorized? ") == "are you legally authorized"


def test_plan_fills_builtins_from_the_profile_and_reports_missing_required():
    plan = by_name(build_plan(JOB, PROFILE))
    assert (plan["first_name"].value, plan["first_name"].source) == ("Chris", "profile")
    assert plan["last_name"].value == "Kim"
    assert plan["preferred_name"].value == "Chris"
    assert plan["email"].value == "chris@example.test"
    assert plan["phone"].value == "+1 415-555-0100"
    assert plan["resume"].source == "resume"
    assert plan["question_1"].value == "https://linkedin.com/in/chris"
    # The cover letter offers a file and a text box; the plan keeps the text.
    assert plan["cover_letter_text"].type == "textarea" and plan["cover_letter_text"].source == "skipped"
    missing = {item.name for item in missing_required(build_plan(JOB, PROFILE))}
    assert missing == {"question_2", "question_3", "question_4[]", "question_5"}


def test_saved_answers_apply_by_label_and_selects_validate_against_options():
    saved = {
        answer_key("How did you hear about this job?"): "handshake",
        answer_key("Why are you interested in this opportunity?"): "The data work.",
        answer_key("What locations are you interested in?"): ["remote", "Nowhere"],
        answer_key("Are you legally authorized to work in the United States?"): "Maybe",
    }
    plan = by_name(build_plan(JOB, PROFILE, saved))
    assert (plan["question_2"].value, plan["question_2"].source) == ("Handshake", "saved")
    assert plan["question_3"].value == "The data work."
    assert plan["question_4[]"].value == ["Remote"]
    # A saved label that is not one of this form's options is dropped, not sent.
    assert plan["question_5"].value is None and plan["question_5"].source == "missing"


def test_explicit_answers_win_and_only_custom_ones_are_banked():
    answers = {"first_name": "Christopher", "question_5": "Yes", "question_2": "Other", "question_3": "Because.", "question_4[]": ["Atlanta, GA"]}
    plan = build_plan(JOB, PROFILE, {}, answers)
    fields = by_name(plan)
    assert (fields["first_name"].value, fields["first_name"].source) == ("Christopher", "answer")
    assert fields["question_5"].value == "Yes"
    assert missing_required(plan) == []
    assert bankable_answers(plan) == {
        answer_key("Are you legally authorized to work in the United States?"): "Yes",
        answer_key("How did you hear about this job?"): "Other",
        answer_key("Why are you interested in this opportunity?"): "Because.",
        answer_key("What locations are you interested in?"): ["Atlanta, GA"],
    }


# -- routes -----------------------------------------------------------------


class FakeRepository:
    def __init__(self):
        self.answers = {}
        self.events = []
        self.updates = []
        self.applications = {
            29: {"id": 29, "external": False, "stage": "considering", "company": "NISC", "title": "Intern",
                 "apply_url_snapshot": "https://job-boards.greenhouse.io/testnisc/jobs/8191885", "resume_variant": "General"},
            30: {"id": 30, "external": False, "stage": "considering", "company": "Micron", "title": "Intern",
                 "apply_url_snapshot": "https://micron.wd1.myworkdayjobs.com/external/job/x", "resume_variant": None},
            31: {"id": 31, "external": False, "stage": "applied", "company": "NISC", "title": "Intern",
                 "apply_url_snapshot": "https://job-boards.greenhouse.io/testnisc/jobs/8191885", "resume_variant": None},
        }

    def for_account(self, account_id):
        return self

    async def get_application(self, application_id):
        return deepcopy(self.applications.get(application_id))

    async def get_apply_answers(self):
        return dict(self.answers)

    async def merge_apply_answers(self, answers):
        self.answers.update(answers)
        return dict(self.answers)

    async def record_application_event(self, application_id, event_type, note=None):
        self.events.append((application_id, event_type, note))
        return True

    async def update_application(self, application_id, changes):
        self.updates.append((application_id, changes))
        self.applications[application_id].update(changes)
        return deepcopy(self.applications[application_id])


class FakeResumes:
    def __init__(self, documents=None):
        self.documents = documents if documents is not None else [
            {"id": 5, "name": "Data"},
            {"id": 6, "name": "General"},
        ]

    def for_account(self, account_id):
        return self

    async def get_profile(self):
        return deepcopy(PROFILE)

    async def list_resumes(self):
        return deepcopy(self.documents)

    async def render_inputs(self, resume_id):
        resume = next((item for item in self.documents if item["id"] == resume_id), None)
        return (deepcopy(PROFILE), resume, [], []) if resume else None


class FakeGreenhouse:
    def __init__(self, ok=True):
        self.ok = ok
        self.submitted = []

    async def fetch_job(self, posting):
        return deepcopy(JOB)

    async def render_resume(self, profile, resume, entries, skills):
        return b"%PDF-fake", f"{resume['name']}.pdf"

    async def submit(self, posting, plan, resume_pdf, resume_filename):
        self.submitted.append((posting, {item.name: item.value for item in plan}, resume_filename))
        if self.ok:
            return SubmissionResult(ok=True, message="Application submitted.", final_url=posting.apply_url + "/confirmation")
        return SubmissionResult(ok=False, message="Greenhouse rejected the form: Phone is required")


def make_client(repository=None, resumes=None, greenhouse=None):
    application = create_app(
        Settings(dsn="injected"),
        repository=repository or FakeRepository(),
        resume_store=resumes or FakeResumes(),
        greenhouse=greenhouse or FakeGreenhouse(),
    )
    application.dependency_overrides[session_account] = lambda: ACCOUNT
    return TestClient(application)


FULL_ANSWERS = {"question_2": "Handshake", "question_3": "The data work.", "question_4[]": ["Remote"], "question_5": "Yes"}


def test_preview_lists_every_field_and_the_resume_named_on_the_card():
    with make_client() as client:
        response = client.post("/api/applications/29/auto-apply/preview")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["board"] == "testnisc" and payload["job_id"] == 8191885
    assert payload["resume"] == {"id": 6, "name": "General"}
    assert payload["resumes"] == [{"id": 5, "name": "Data"}, {"id": 6, "name": "General"}]
    assert [field["name"] for field in payload["fields"]][:2] == ["first_name", "last_name"]
    assert payload["missing"] == ["question_2", "question_3", "question_4[]", "question_5"]


def test_preview_refuses_non_greenhouse_and_already_applied_cards():
    with make_client() as client:
        assert client.post("/api/applications/30/auto-apply/preview").status_code == 422
        assert client.post("/api/applications/31/auto-apply/preview").status_code == 409
        assert client.post("/api/applications/99/auto-apply/preview").status_code == 404


def test_submit_requires_confirmation_and_every_required_answer():
    greenhouse = FakeGreenhouse()
    with make_client(greenhouse=greenhouse) as client:
        unconfirmed = client.post("/api/applications/29/auto-apply", json={"answers": FULL_ANSWERS})
        assert unconfirmed.status_code == 422
        partial = client.post("/api/applications/29/auto-apply", json={"answers": {"question_5": "Yes"}, "confirm": True})
        assert partial.status_code == 422
        assert "How did you hear" in partial.json()["detail"]
    assert greenhouse.submitted == []


def test_confirmed_submit_drives_the_form_then_moves_the_card_to_applied():
    repository, greenhouse = FakeRepository(), FakeGreenhouse()
    with make_client(repository=repository, greenhouse=greenhouse) as client:
        response = client.post("/api/applications/29/auto-apply", json={"answers": FULL_ANSWERS, "confirm": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["application"]["stage"] == "applied"
    assert payload["result"]["resume"] == {"id": 6, "name": "General"}

    posting, typed, filename = greenhouse.submitted[0]
    assert posting.board == "testnisc" and filename == "General.pdf"
    assert typed["first_name"] == "Chris" and typed["question_2"] == "Handshake" and typed["question_4[]"] == ["Remote"]

    assert repository.updates == [(29, {"stage": "applied", "resume_variant": "General"})]
    assert repository.events[-1][1] == "auto_applied"
    # Custom answers are banked for the next form; builtins are not.
    assert repository.answers[answer_key("Are you legally authorized to work in the United States?")] == "Yes"
    assert "first name" not in " ".join(repository.answers)


def test_a_refused_form_leaves_the_card_in_considering():
    repository, greenhouse = FakeRepository(), FakeGreenhouse(ok=False)
    with make_client(repository=repository, greenhouse=greenhouse) as client:
        response = client.post("/api/applications/29/auto-apply", json={"answers": FULL_ANSWERS, "confirm": True})
    assert response.status_code == 502
    assert "Phone is required" in response.json()["detail"]
    assert repository.updates == []
    assert repository.applications[29]["stage"] == "considering"
    assert repository.events == [(29, "auto_apply_failed", "Greenhouse rejected the form: Phone is required")]


def test_submit_without_any_resume_is_refused_before_the_browser_runs():
    greenhouse = FakeGreenhouse()
    with make_client(resumes=FakeResumes(documents=[]), greenhouse=greenhouse) as client:
        response = client.post("/api/applications/29/auto-apply", json={"answers": FULL_ANSWERS, "confirm": True})
    assert response.status_code == 422
    assert "resume" in response.json()["detail"].lower()
    assert greenhouse.submitted == []
