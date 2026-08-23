"""Sign-in flow: the gate, the allowlist, and what happens without a session.

The Google round trip is stubbed at the two seams that talk to the network —
``exchange_code`` and ``fetch_userinfo`` — so these tests exercise our own
decisions (state check, verified check, allowlist, session write) rather than
Google's.
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from fake_resume_store import InMemoryResumeStore

from oneshot_ui import auth_api
from oneshot_ui.app import create_app
from oneshot_ui.config import Settings
from oneshot_ui.repository import LOCAL_ACCOUNT_ID

ACCOUNT_ID = UUID("00000000-0000-0000-0000-0000000000aa")

ALLOWED = "invited@example.test"
SETTINGS = Settings(
    dsn="injected",
    google_client_id="client-id",
    google_client_secret="client-secret",
    session_secret="test-secret",
    allowed_emails=frozenset({ALLOWED}),
    public_url="http://testserver",
)


class NullRepository:
    def __init__(self) -> None:
        self.scoped_to: list = []
        self.job_options: list[dict] = []

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def for_account(self, account_id):
        self.scoped_to.append(account_id)
        return self

    async def list_applications(self):
        return {"applications": [], "total": 0}

    async def list_jobs(self, **options):
        self.job_options.append(options)
        return {"jobs": [], "total": 0}

    async def source_coverage(self):
        return {"sources": [], "total_coverage": 0}

    async def board_map(self):
        return {"providers": [], "totals": {"boards": 0, "providers": 0, "ok": 0, "erroring": 0, "stale": 0, "paused": 0, "never": 0, "added_today": 0}}


class FakeAuthStore:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def find_or_create_account(self, **fields):
        self.created.append(fields)
        return ACCOUNT_ID

    async def account_profile(self, account_id):
        if not self.created:
            return None
        last = self.created[-1]
        return {
            "email": last["email"],
            "name": last["name"],
            "picture_url": last["picture_url"],
        }


def make_client(
    settings: Settings = SETTINGS,
) -> tuple[TestClient, FakeAuthStore, NullRepository]:
    auth_store = FakeAuthStore()
    repository = NullRepository()
    application = create_app(
        settings,
        repository=repository,
        # Signed-in cases in this suite reach resume routing, so keep a fake
        # store available; anonymous requests are rejected before it is used.
        resume_store=InMemoryResumeStore(),
        auth_store=auth_store,
    )
    return TestClient(application, follow_redirects=False), auth_store, repository


def stub_google(monkeypatch, userinfo: dict) -> None:
    async def fake_exchange(code, **kwargs):
        return "access-token"

    async def fake_userinfo(access_token):
        return userinfo

    monkeypatch.setattr(auth_api, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth_api, "fetch_userinfo", fake_userinfo)


def begin_sign_in(client: TestClient) -> str:
    """Drive /login so the session holds a state, and return that state."""
    response = client.get("/api/auth/login")
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fauth%2Fcallback" in location
    return location.split("state=")[1].split("&")[0]


def test_public_reads_and_pages_stay_open_but_workspace_apis_require_sign_in() -> None:
    """Anonymous CRUD is browser-local; the shared sentinel is never writable."""
    client, _, repository = make_client()
    with client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/source-coverage").status_code == 200
        assert client.get("/api/source-coverage/boards").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/login/").status_code == 200

        assert client.get("/api/applications").status_code == 401
        assert client.post(
            "/api/applications/external",
            json={"company": "Local", "title": "Intern", "url": "https://example.test/job"},
        ).status_code == 401
        assert client.post("/api/jobs/1/applications").status_code == 401
        assert client.get("/api/resume/profile").status_code == 401
        assert client.put("/api/resume/profile", json={"full_name": "Private"}).status_code == 401
        assert client.get("/api/resume/resumes").status_code == 401

        assert client.get("/api/auth/me").status_code == 401

        # Only public job/source reads touch the sentinel. Private dependencies
        # reject the request before a store can be scoped to it.
        assert set(repository.scoped_to) == {LOCAL_ACCOUNT_ID}


def test_an_anonymous_reader_is_scoped_to_the_sentinel_not_to_a_real_account(
    monkeypatch,
) -> None:
    """`list_jobs` hides postings you have already applied to, using account_id.

    Anonymous visitors must not inherit some signed-in user's hidden set, and a
    signed-in user must still get their own. Both go through the same route, so
    the only thing separating them is which account it is scoped to.
    """
    client, _, repository = make_client()
    with client:
        client.get("/api/jobs")
        assert repository.scoped_to == [LOCAL_ACCOUNT_ID]
        assert repository.job_options[-1]["hide_tracked"] is False

        state = begin_sign_in(client)
        stub_google(monkeypatch, {
            "sub": "1", "email": ALLOWED, "email_verified": True, "name": "Invited",
        })
        client.get("/api/auth/callback", params={"code": "abc", "state": state})

        client.get("/api/jobs")
        assert repository.scoped_to == [LOCAL_ACCOUNT_ID, ACCOUNT_ID]
        assert repository.job_options[-1]["hide_tracked"] is True


def test_invited_account_signs_in_and_gets_a_session(monkeypatch) -> None:
    client, auth_store, repository = make_client()
    with client:
        state = begin_sign_in(client)
        stub_google(monkeypatch, {
            "sub": "1234567890",
            "email": ALLOWED.upper(),  # Google may return any casing
            "email_verified": True,
            "name": "Invited Person",
            "picture": "https://example.test/avatar.png",
        })
        response = client.get("/api/auth/callback", params={"code": "abc", "state": state})
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        # The email is normalised before it reaches the database.
        assert auth_store.created[-1]["email"] == ALLOWED
        assert auth_store.created[-1]["google_sub"] == "1234567890"

        assert client.get("/api/auth/me").json()["name"] == "Invited Person"
        # The repository was scoped to the account the session names.
        assert client.get("/api/applications").status_code == 200
        assert repository.scoped_to == [ACCOUNT_ID]

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize(
    ("userinfo", "expected"),
    [
        (
            {"sub": "1", "email": "stranger@example.test", "email_verified": True, "name": "S"},
            "/login/?error=not_invited",
        ),
        (
            {"sub": "1", "email": ALLOWED, "email_verified": False, "name": "U"},
            "/login/?error=unverified",
        ),
    ],
)
def test_callback_refuses_uninvited_and_unverified_accounts(
    monkeypatch, userinfo, expected
) -> None:
    client, auth_store, repository = make_client()
    with client:
        state = begin_sign_in(client)
        stub_google(monkeypatch, userinfo)
        response = client.get("/api/auth/callback", params={"code": "abc", "state": state})

        assert response.status_code == 303
        assert response.headers["location"] == expected
        assert auth_store.created == []          # no account was created
        # No session was written either: private workspace data stays gated.
        assert client.get("/api/applications").status_code == 401
        assert repository.scoped_to == []


def test_callback_rejects_a_mismatched_state(monkeypatch) -> None:
    client, auth_store, repository = make_client()
    with client:
        begin_sign_in(client)
        stub_google(monkeypatch, {
            "sub": "1", "email": ALLOWED, "email_verified": True, "name": "Invited",
        })
        response = client.get(
            "/api/auth/callback", params={"code": "abc", "state": "forged"}
        )

        assert response.headers["location"] == "/login/?error=bad_state"
        assert auth_store.created == []


def test_login_reports_missing_configuration_instead_of_bouncing_to_google() -> None:
    client, _, _repo = make_client(Settings(dsn="injected", session_secret="test-secret"))
    with client:
        response = client.get("/api/auth/login")

    assert response.status_code == 503
    assert "GOOGLE_CLIENT_ID" in response.json()["detail"]
