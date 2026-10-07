from __future__ import annotations

import asyncio
from functools import partial

import httpx
import pytest

from xjobscore.inspector import token_sweep, validator
from xjobscore.inspector.token_sweep import (
    THROTTLED_REASONS, SweepAlreadyRunning, SweepRunner, _round_robin, net_request,
)
from xjobscore.net import Net, NetError, Response


class FakePool:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        self.muted: set[tuple[str, str]] = set()
        self.executed: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        rows = [row for row in self.rows if row not in self.muted] if "NOT muted" in sql else self.rows
        if "GROUP BY ats" in sql:
            counts: dict[str, int] = {}
            for ats, _ in rows:
                counts[ats] = counts.get(ats, 0) + 1
            return [{"ats": ats, "n": n} for ats, n in counts.items()]
        if args and "NOT (ats" in sql:
            rows = [row for row in rows if row[0] not in args[0]]
        elif args:
            rows = [row for row in rows if row[0] in args[0]]
        return [{"ats": ats, "token": token} for ats, token in rows]

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))


class FakePolicy:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def pool_or_none(self):
        return self.pool


class FakeNet:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def runner(rows, probe, **kwargs) -> tuple[SweepRunner, FakePool]:
    pool = FakePool(rows)
    sweep = SweepRunner(FakePolicy(pool), probe=probe, net_factory=FakeNet, **kwargs)
    return sweep, pool


def written(pool: FakePool) -> dict[tuple[str, str], tuple[str, bool]]:
    """(ats, token) -> (status, logged) for every persisted result."""
    out = {}
    for sql, args in pool.executed:
        status, _, ats, token, _ = args
        out[(ats, token)] = (status, "token_log" in sql)
    return out


async def finish(sweep: SweepRunner) -> dict:
    assert sweep._task is not None
    await asyncio.wait_for(sweep._task, timeout=5)
    return sweep.snapshot()


def test_round_robin_takes_from_every_vendor():
    rows = [("a", f"a{i}") for i in range(10)] + [("b", "b0"), ("b", "b1")]
    picked = _round_robin(rows, 4)
    assert {k: list(v) for k, v in picked.items()} == {"a": ["a0", "a1"], "b": ["b0", "b1"]}
    assert sum(len(v) for v in _round_robin(rows, None).values()) == 12


async def test_initial_rate_uses_elapsed_time_instead_of_a_full_window():
    now = [100.0]
    started = asyncio.Event()
    finish_probe = asyncio.Event()

    async def probe(ats, token, *, request):
        started.set()
        await finish_probe.wait()
        return "live", {}

    sweep, _ = runner([("greenhouse", f"p{i}") for i in range(10)], probe, clock=lambda: now[0])
    await sweep.start(limit=None, ats=["greenhouse"])
    await started.wait()
    vendor = sweep._vendors["greenhouse"]
    vendor.stamps.extend([105.0, 106.0, 107.0, 108.0])
    now[0] = 118.0
    assert sweep.snapshot()["rate"] == 0.22  # four answers in 18s, not 30s
    now[0] = 131.0
    assert sweep.snapshot()["rate"] == 0.13
    await sweep.cancel()


@pytest.mark.parametrize("ats", [None, ["peopleadmin"]])
async def test_muted_tokens_are_excluded_from_backlog_and_sweep(ats):
    async def probe(ats, token, *, request):
        assert token == "keep"
        return "live", {}

    sweep, pool = runner([("peopleadmin", "mute"), ("peopleadmin", "keep")], probe)
    pool.muted.add(("peopleadmin", "mute"))
    assert await sweep.backlog() == {"peopleadmin": 1}
    await sweep.start(limit=None, ats=ats)
    snap = await finish(sweep)
    assert (snap["total"], snap["done"]) == (1, 1)
    assert written(pool) == {("peopleadmin", "keep"): ("live", True)}


@pytest.mark.asyncio
async def test_sweep_persists_and_logs_only_live():
    verdicts = {"t1": "live", "t2": "dead", "t3": "unknown"}

    async def probe(ats, token, *, request):
        return verdicts[token], {}

    sweep, pool = runner([("greenhouse", t) for t in verdicts], probe)
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert snap["status"] == "finished"
    assert (snap["done"], snap["live"], snap["dead"], snap["unclear"]) == (3, 1, 1, 1)
    assert written(pool) == {
        ("greenhouse", "t1"): ("live", True),
        ("greenhouse", "t2"): ("dead", False),
        ("greenhouse", "t3"): ("unknown", False),
    }


@pytest.mark.asyncio
async def test_throttled_vendor_cools_alone_and_never_persists_refusals(monkeypatch):
    monkeypatch.setattr(token_sweep, "RECOVER_STREAK", 1)
    refusals = {"w1": 1}

    async def probe(ats, token, *, request):
        if refusals.get(token):
            refusals[token] -= 1
            return "unknown", {"reason": "http_429"}
        return "live", {}

    sweep, pool = runner(
        [("workable", "w1"), ("greenhouse", "g1"), ("greenhouse", "g2")], probe, cooldowns=(0.05,),
    )
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert snap["done"] == 3
    # The refused probe was retried, not written as checked.
    assert len(pool.executed) == 3
    events = [(e["vendor"], e["text"]) for e in snap["events"]]
    assert ("workable", "429 · cooling 1m") in events
    assert ("workable", "recovered") in events
    assert not any(vendor == "greenhouse" and "cooling" in text for vendor, text in events)


@pytest.mark.asyncio
async def test_vendor_gives_up_after_max_strikes(monkeypatch):
    monkeypatch.setattr(token_sweep, "MAX_STRIKES", 2)

    async def probe(ats, token, *, request):
        if ats == "oracle":
            return "unknown", {"reason": "blocked"}
        return "dead", {}

    oracle = [("oracle", f"o{i}") for i in range(4)]
    sweep, pool = runner([*oracle, ("lever", "l1")], probe, cooldowns=(0.01,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    states = {v["ats"]: v["state"] for v in snap["vendors"]}
    assert states == {"oracle": "failing", "lever": "done"}
    assert set(written(pool)) == {("lever", "l1")}


@pytest.mark.asyncio
async def test_pause_cancel_and_single_sweep():
    gate = asyncio.Event()

    async def probe(ats, token, *, request):
        await gate.wait()
        return "live", {}

    sweep, _ = runner([("ashby", f"t{i}") for i in range(20)], probe)
    await sweep.start(limit=5)
    assert sweep.snapshot()["total"] == 5
    with pytest.raises(SweepAlreadyRunning):
        await sweep.start(limit=5)
    assert sweep.pause(True)["status"] == "paused"
    assert sweep.pause(False)["status"] == "running"
    snap = await sweep.cancel()
    assert snap["status"] == "cancelled"
    assert not sweep.running


@pytest.mark.asyncio
async def test_net_request_turns_refusals_into_statuses():
    class Net:
        async def get(self, url, **kw):
            raise NetError("HTTP 429", url=url, status=429)

        async def head(self, url, **kw):
            raise NetError("boom", url=url)

    refused = await net_request(Net(), "GET", "https://x")
    assert refused is not None and refused.status_code == 429 and refused.text == ""
    assert await net_request(Net(), "HEAD", "https://x") is None


@pytest.mark.asyncio
async def test_workday_disabled_site_is_persisted_dead_not_throttled():
    """A disabled Workday site's 403 carries an S22 body; the sweep must read it
    through `Net` and record the token dead instead of cooling the vendor."""
    body = b'{"errorCode":"S22","httpStatus":403,"message":"permission denied"}'

    class Net:
        async def post(self, url, **kw):
            response = Response(url=url, status=403, headers={"content-type": "application/json"}, body=body)
            raise NetError("HTTP 403", url=url, status=403, response=response)

    status, details = await validator.probe_token(
        "workday", "acme/wd1/old", request=partial(net_request, Net()),
    )
    assert (status, details["reason"]) == ("dead", "site_disabled")
    assert details["reason"] not in THROTTLED_REASONS


@pytest.mark.parametrize("code", [429, 403])
async def test_employer_redirect_refusals_do_not_stall_vendor(code):
    """Even a redirected RSS endpoint must not let one employer park the ATS."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host in {"bad1.teamtailor.com", "bad2.teamtailor.com"}:
            return httpx.Response(301, headers={"location": "https://employer.example/jobs"})
        if request.url.host == "employer.example":
            return httpx.Response(code, text="<html>Cloudflare: Just a moment...</html>")
        return httpx.Response(200, text='<rss version="2.0"><channel/></rss>')

    class MockNet(Net):
        async def __aenter__(self):
            self._client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=True,
            )
            return self

    pool = FakePool([("teamtailor", t) for t in ["bad1", "bad2", "healthy"]])
    sweep = SweepRunner(
        FakePolicy(pool), net_factory=lambda: MockNet(user_agent="test", max_retries=0),
    )
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert snap["status"] == "finished"
    assert (snap["done"], snap["live"], snap["skipped"]) == (1, 1, 2)
    vendor = snap["vendors"][0]
    assert vendor["strikes"] == 0
    assert vendor["workers"] == token_sweep._width("teamtailor", "healthy")
    assert written(pool) == {("teamtailor", "healthy"): ("live", True)}
    assert not any("cooling" in event["text"] for event in snap["events"])
    assert sum("bad1.teamtailor.com" in url for url in seen) == 2


@pytest.mark.parametrize("host, token_only", [
    ("careers.employer.example", True),
    ("notteamtailor.com", True),
    ("other.teamtailor.com", False),
])
async def test_net_adapter_preserves_refusal_origin(host, token_only):
    class RefusingNet:
        async def get(self, url, **kwargs):
            final_url = f"https://{host}/jobs.rss"
            response = Response(url=final_url, status=429, headers={}, body=b"")
            raise NetError("HTTP 429", url=final_url, status=429, response=response)

    status, details = await validator.probe_token(
        "teamtailor", "acme", request=partial(net_request, RefusingNet()),
    )
    assert status == "unknown"
    assert details["reason"] == "http_429"
    assert (details.get("refusal_scope") == "token") == token_only
    if token_only:
        assert details["final_url"] == f"https://{host}/jobs.rss"


@pytest.mark.asyncio
async def test_one_answer_between_refusals_does_not_reset_strikes(monkeypatch):
    # The loop this guards against: refuse, cool 1m, answer once, strikes back
    # to zero, refuse again -- a vendor that never escalated past one minute.
    monkeypatch.setattr(token_sweep, "MAX_STRIKES", 3)
    calls = {"n": 0}

    async def probe(ats, token, *, request):
        calls["n"] += 1
        # Alternate: refused, answered, refused, answered ...
        return ("unknown", {"reason": "http_429"}) if calls["n"] % 2 else ("live", {})

    sweep, _ = runner([("adp", f"a{i}") for i in range(10)], probe, cooldowns=(0.01,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    adp = snap["vendors"][0]
    assert adp["state"] == "failing"
    assert adp["strikes"] == 3
    assert not any(e["text"] == "recovered" for e in snap["events"])


@pytest.mark.asyncio
async def test_refusal_drops_to_one_worker_and_answers_widen_again(monkeypatch):
    monkeypatch.setattr(token_sweep, "RECOVER_STREAK", 2)
    in_flight = {"now": 0, "peak_after_refusal": 0}
    refused = {"done": False}

    async def probe(ats, token, *, request):
        in_flight["now"] += 1
        try:
            if not refused["done"]:
                refused["done"] = True
                return "unknown", {"reason": "http_429"}
            await asyncio.sleep(0.01)
            return "live", {}
        finally:
            if refused["done"]:
                in_flight["peak_after_refusal"] = max(in_flight["peak_after_refusal"], in_flight["now"])
            in_flight["now"] -= 1

    sweep, _ = runner([("ashby", f"t{i}") for i in range(12)], probe, cooldowns=(0.01,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    vendor = snap["vendors"][0]
    assert vendor["done"] == 12
    # Widened back up after enough answers, but never past the sweep's ceiling.
    assert 1 < vendor["workers"] <= token_sweep.MAX_WIDTH


@pytest.mark.asyncio
async def test_one_walled_tenant_is_skipped_without_cooling_the_vendor():
    """The stall this guards against: one tenant's 403 requeued first after
    every cooldown until the whole vendor was given up."""
    async def probe(ats, token, *, request):
        return ("unknown", {"reason": "blocked"}) if token == "bad" else ("live", {})

    sweep, pool = runner([("workday", t) for t in ("bad", "a", "b", "c")], probe, cooldowns=(60.0,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    vendor = snap["vendors"][0]
    assert (vendor["state"], vendor["done"], vendor["skipped"], vendor["strikes"]) == ("done", 3, 1, 0)
    assert ("workday", "bad") not in written(pool)
    assert not any("cooling" in e["text"] for e in snap["events"])


@pytest.mark.asyncio
async def test_plain_403_is_an_answer_not_a_refusal():
    async def probe(ats, token, *, request):
        return "unknown", {"reason": "http_403"}

    sweep, pool = runner([("workday", "a"), ("workday", "b")], probe, cooldowns=(60.0,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert (snap["done"], snap["unclear"], snap["skipped"]) == (2, 2, 0)


@pytest.mark.asyncio
async def test_moved_token_is_persisted_alongside_the_dead_one():
    async def probe(ats, token, *, request):
        return "dead", {"reason": "wrong_shard", "moved_to": "acme/wd1/site"}

    sweep, pool = runner([("workday", "acme/wd5/site")], probe)
    await sweep.start(limit=None)
    await finish(sweep)
    (first_sql, first), (moved_sql, moved) = pool.executed
    assert first[0] == "dead" and first[3] == "acme/wd5/site"
    assert "INSERT INTO token " in moved_sql and moved[1:3] == ("acme/wd5/site", "acme/wd1/site")


@pytest.mark.asyncio
async def test_transport_failures_skip_the_token_without_cooling_the_vendor():
    """The stall this guards against: a few dead tenant hosts at the head of
    the queue cooled avature/taleo/eightfold until they gave up at 0 checked."""
    tries: dict[str, int] = {}

    async def probe(ats, token, *, request):
        tries[token] = tries.get(token, 0) + 1
        return ("unknown", {"reason": "transport"}) if token.startswith("dead") else ("live", {})

    rows = [("taleo", t) for t in ("dead1", "dead2", "dead3", "a", "b")]
    sweep, pool = runner(rows, probe, cooldowns=(60.0,))
    await sweep.start(limit=None)
    snap = await finish(sweep)
    vendor = snap["vendors"][0]
    assert (vendor["state"], vendor["done"], vendor["skipped"], vendor["strikes"]) == ("done", 2, 3, 0)
    # Never dropped to one worker, as a refusal would.
    assert vendor["workers"] == token_sweep._width("taleo", "a")
    assert {t: n for t, n in tries.items() if t.startswith("dead")} == {"dead1": 2, "dead2": 2, "dead3": 2}
    assert set(written(pool)) == {("taleo", "a"), ("taleo", "b")}
    assert not any("cooling" in e["text"] for e in snap["events"])


@pytest.mark.asyncio
async def test_disabled_vendor_is_omitted_and_cannot_be_requested():
    async def probe(ats, token, *, request):
        return "live", {}

    rows = [("paylocity", "p1"), ("greenhouse", "g1")]
    sweep, pool = runner(rows, probe)
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert [v["ats"] for v in snap["vendors"]] == ["greenhouse"]
    assert all(e["vendor"] != "paylocity" for e in snap["events"])
    assert "paylocity" not in await sweep.backlog()

    sweep, pool = runner(rows, probe)
    with pytest.raises(ValueError, match="disabled"):
        await sweep.start(limit=None, ats=["paylocity"])


@pytest.mark.parametrize("refused", [False, True])
async def test_html_control_is_shared_and_refusals_leave_tokens_unchecked(monkeypatch, refused):
    monkeypatch.setattr(token_sweep, "MAX_STRIKES", 1)
    controls = []
    rule = token_sweep.BY_SOURCE["jobvite"]

    class ControlNet(FakeNet):
        async def get(self, url, **kwargs):
            if url.endswith(rule.html_control_token):
                controls.append(url)
                await asyncio.sleep(0)
                if refused:
                    raise NetError("HTTP 429", url=url, status=429)
                body = b"<html><title>Error</title></html>"
            else:
                body = b"<html><title>Acme - Job Opportunities</title></html>"
            return Response(url=url, status=200, headers={}, body=body)

    pool = FakePool([("jobvite", f"p{i}") for i in range(4)])
    sweep = SweepRunner(FakePolicy(pool), net_factory=ControlNet, cooldowns=(0.01,))
    await sweep.start(limit=None, ats=["jobvite"])
    snap = await finish(sweep)
    assert len(controls) == 1
    if refused:
        assert (snap["done"], snap["live"]) == (0, 0)
        assert written(pool) == {}
        assert snap["vendors"][0]["state"] == "failing"
        assert snap["events"]
    else:
        assert (snap["done"], snap["live"]) == (4, 4)


def test_sweep_net_bounds_connection_setup():
    net = SweepRunner(FakePolicy(None))._net_factory()
    assert net._connect_timeout_s == token_sweep.CONNECT_TIMEOUT_S


def test_width_never_exceeds_sweep_ceiling():
    # myworkdayjobs.com carries a feed cap of 24; a sweep must not use it all.
    assert token_sweep._width("workday", "acme/wd5/careers") == token_sweep.SWEEP_WIDTH["workday"] < 24
    assert token_sweep._width("eightfold", "acme") <= token_sweep.MAX_WIDTH


@pytest.mark.asyncio
async def test_paced_vendor_spaces_probe_starts(monkeypatch):
    monkeypatch.setitem(token_sweep.SWEEP_PACE_S, "workable", 0.02)
    starts: list[float] = []

    async def probe(ats, token, *, request):
        starts.append(asyncio.get_running_loop().time())
        return "live", {}

    sweep, _ = runner([("workable", f"w{i}") for i in range(6)], probe)
    await sweep.start(limit=None)
    snap = await finish(sweep)
    assert snap["done"] == 6
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 0.015
