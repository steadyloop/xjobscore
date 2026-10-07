"""Keep persistent outbound policy state out of production during tests."""
import pytest


@pytest.fixture(autouse=True)
def isolate_workable_policy_state(tmp_path, monkeypatch, request):
    # Explicit live checks must still share the production egress policy.
    if request.node.get_closest_marker("live") is not None:
        return
    monkeypatch.setenv("XJOBSCORE_WORKABLE_STATE", str(tmp_path / "workable-policy.sqlite3"))
