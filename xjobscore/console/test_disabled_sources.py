"""Operator-disabled vendors stay hidden and cannot be fetched or re-enabled."""

import pytest
from fastapi.testclient import TestClient

from xjobscore import sources
from xjobscore.console.test_support import create_app
from xjobscore.console.db import feed_ref
from xjobscore.console.test_routes import FakeCatalog, FakePolicy
from xjobscore.records import Feed


def test_paylocity_is_hidden_and_stale_controls_cannot_reenable_it(app_store, monkeypatch):
    policy = FakePolicy({})
    ref = feed_ref("paylocity", "retired")
    # Even adding an adapter later must not undo an operator's explicit block.
    monkeypatch.setitem(sources.SOURCES, "paylocity", object())
    app = create_app(
        catalog=FakeCatalog(Feed(id=1, source="paylocity", key="retired", config={})),
        store=app_store,
        policy=policy,
    )
    with TestClient(app) as client:
        assert client.get("/api/feeds").json()["feeds"] == []
        assert client.get("/api/scheduler/feeds").json()["feeds"] == []
        assert "paylocity" not in client.get("/api/system/live").json()["workers"]
        assert client.post(f"/api/feeds/{ref}/policy", json={"enabled": True}).status_code == 404
        assert client.post("/api/feeds/bulk-policy", json={"refs": [ref], "enabled": True}).status_code == 404
    assert policy.calls == []


def test_disabled_vendor_cannot_be_fetched_even_if_an_adapter_is_registered(monkeypatch):
    monkeypatch.setitem(sources.SOURCES, "paylocity", object())
    with pytest.raises(ValueError, match="disabled"):
        sources.get("paylocity")
    with pytest.raises(ValueError, match="disabled"):
        sources.reusing("paylocity", None)
