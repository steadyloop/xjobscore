"""Validate the never-checked backlog in ``token``, one worker pool per vendor.

The heartbeat re-probes dead tokens twenty at a time; this is its bulk
sibling for the ``status = 'unknown' AND checked_at IS NULL`` rows keyscan
imports by the tens of thousands. Started from the Tokens page with a backlog
size, it runs in-process and simply stops if the console does -- nothing is
lost, because progress *is* the token rows: a checked token leaves the
never-checked set, so the next Start continues on what is left.

Rate limits are vendor-specific, so the sweep is shaped like the feed poller
(``run.py``'s ``cycle``): every vendor gets its own queue and workers, sized by
``net.vendor_cap``, and HTTP goes through ``xjobscore.net.Net`` for its
host-wide 429 pacing. A vendor that keeps refusing cools down on its own while
every other vendor carries on.

``keyscan.probe`` folds a 429 or 403 into ``inconclusive``; written as-is
that would mark a token checked when it was only refused. A throttled probe
is therefore never persisted -- the token goes back on its vendor's queue.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
import logging
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from keyscan.probe import HTMLControlCache
from keyscan.rules import BY_SOURCE
from xjobscore.source_policy import require_source_allowed, source_disabled, visible_source_sql
from xjobscore.console import validator
from xjobscore.net import DEFAULT_USER_AGENT, Net, NetError, vendor_cap

logger = logging.getLogger(__name__)

# Workers per vendor when `net.vendor_cap` names no ceiling for its host. Each
# also becomes the host's in-flight limit in `Net`, so a single-host vendor
# (api.ashbyhq.com) never sees more than this many requests at once.
DEFAULT_WIDTH = 4
# Ceiling on any vendor's workers, whatever `vendor_cap` allows. The caps are
# sized for polling known boards; probing thousands of unknown ones in a row
# reads as enumeration.
MAX_WIDTH = 4
# Measured exceptions to `MAX_WIDTH`. Workday puts every tenant on its own
# host, so width spreads across tenants rather than piling onto one. The 403s
# once blamed on width were disabled sites (S22), not walls: 2026-10-05, 360
# backlog tokens at 4/8/16 workers drew no wall and no 429, at 2.0/6.1/7.5
# tokens/s. Still under its feed cap of 24.
SWEEP_WIDTH = {"workday": 16}
# Fixed spacing between probe starts for a vendor that rate-limits hard, so
# the sweep stays under the limit instead of bursting into a 429 and cooling.
# Workable (one host, apply.workable.com), 2026-10-05: a fresh client got 106
# requests at 7.3/s and 194 at 4.6/s before a 429, and 3/s failed after ~79.
# The block outlives the burst and grows with repeat offences -- requests made
# while blocked kept it going, and after several it held through 2 quiet
# minutes. The sustainable rate is unmeasured; 1/s is a guess, not a limit.
# Provisional 0.4/s verification pace, below the clean 25-minute 0.425/s
# experiment. SharedPolicy also spaces discovery/manual validation globally.
SWEEP_PACE_S = {"workable": 2.5}
# Consecutive answered probes that clear a vendor's strikes and reopen one more
# worker. A single success used to clear them, so a vendor that answered once
# between refusals looped on one-minute cooldowns forever instead of escalating.
RECOVER_STREAK = 20
# Cooldown after the 1st, 2nd and 3rd+ consecutive throttled probe.
COOLDOWN_S = (60.0, 300.0, 900.0)
# Consecutive throttled probes before a vendor is given up for this sweep.
MAX_STRIKES = 5
# Probe outcomes that mean "refused", not "answered". A 403 counts only when
# the probe read it as a bot wall (`blocked`): Workday's per-site 403s are
# answers, and treating them as refusals once stalled a whole sweep on one
# disabled site.
THROTTLED_REASONS = frozenset({"http_429", "blocked", "transport"})
# Refusals of one token, other than a 429, before this sweep skips it. It stays
# never-checked, so the next sweep tries it again.
MAX_TOKEN_REFUSALS = 2
# Distinct tokens refused since the last answer before a bot wall cools the
# vendor: one tenant's wall says nothing about the rest.
REFUSALS_TO_COOL = 2
# A `transport` failure never cools a vendor. It is one host failing to
# connect, and on vendors that give every tenant its own host that is a dead
# tenant, not a limit: 2026-10-06 the first queued tokens of avature, taleo,
# eightfold and peopleadmin were all dead hosts, and their cooldowns left
# every one of those vendors at 0 checked. The token is still retried and
# skipped like any refusal, so it stays never-checked.
# Vendors "All vendors" leaves out; picking one by name still sweeps it.
# paylocity: no adapter polls it, so a live verdict feeds nothing, and its
# 12k-token backlog was two thirds of a whole sweep.
SWEEP_SKIP = frozenset({"paylocity"})
# Connection setup bound for the sweep's `Net`. A dead peopleadmin tenant's
# host never accepts, and at the 15s read timeout each probe took ~64s.
CONNECT_TIMEOUT_S = 5.0
RATE_WINDOW_S = 30.0
MAX_EVENTS = 50
BACKLOG_TTL_S = 30.0

_NEVER_CHECKED = (
    "status = 'unknown' AND checked_at IS NULL AND NOT muted"
    f" AND {visible_source_sql('ats')}"
)


class SweepAlreadyRunning(RuntimeError):
    pass


class _ProbeResponse:
    """The httpx-shaped face `keyscan.probe` reads, over a `net.Response`."""

    __slots__ = ("status_code", "headers", "url", "_response")

    def __init__(self, status_code: int, headers: dict[str, str], response: Any = None, *, url: str = "") -> None:
        self.status_code = status_code
        self.headers = headers
        self.url = response.url if response is not None else url
        self._response = response

    def json(self) -> Any:
        if self._response is None:
            raise ValueError("no body")
        try:
            return self._response.json()
        except NetError as exc:  # the probe expects httpx's ValueError
            raise ValueError(str(exc)) from exc

    @property
    def text(self) -> str:
        return self._response.text if self._response is not None else ""


async def net_request(
    net: Net, method: str, url: str, *, json_body: dict | None = None,
    headers: dict[str, str] | None = None, concurrency: int = 1,
) -> _ProbeResponse | None:
    """A `keyscan.probe.RequestFn` over `Net`.

    `Net` raises on any final non-2xx; the probe wants to read the status, so
    a refusal comes back as a response carrying it (and its body, when `Net`
    kept one), and a transport failure as None -- the probe's own "could not
    tell".
    """
    try:
        if method == "HEAD":
            response = await net.head(url, headers=headers, concurrency=concurrency)
        elif method == "POST":
            response = await net.post(url, json=json_body, headers=headers, concurrency=concurrency)
        else:
            response = await net.get(url, headers=headers, concurrency=concurrency)
    except NetError as exc:
        if exc.status is None:
            return None
        refused = exc.response
        return _ProbeResponse(exc.status, refused.headers if refused else {}, refused, url=exc.url)
    return _ProbeResponse(response.status, response.headers, response)


def _width(ats: str, token: str) -> int:
    rule = BY_SOURCE.get(ats)
    if rule is None:
        return 1
    try:
        url = (rule.light_url or rule.probe_url or rule.board_url).format(**rule.urls(token))
    except (KeyError, IndexError):
        return DEFAULT_WIDTH
    host = urlsplit(url).netloc
    ceiling = SWEEP_WIDTH.get(ats, MAX_WIDTH)
    return min(ceiling, (vendor_cap(host) or DEFAULT_WIDTH) if host else DEFAULT_WIDTH)


def _round_robin(rows: list[tuple[str, str]], limit: int | None) -> dict[str, deque[str]]:
    """Take up to `limit` rows, one vendor at a time, so every vendor moves."""
    by_vendor: dict[str, deque[str]] = {}
    for ats, token in rows:
        by_vendor.setdefault(ats, deque()).append(token)
    if limit is None or limit >= len(rows):
        return by_vendor
    picked: dict[str, deque[str]] = {ats: deque() for ats in by_vendor}
    taken = 0
    while taken < limit:
        for ats, queue in by_vendor.items():
            if queue and taken < limit:
                picked[ats].append(queue.popleft())
                taken += 1
    return {ats: queue for ats, queue in picked.items() if queue}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Vendor:
    queue: deque[str]
    width: int
    total: int
    done: int = 0
    live: int = 0
    dead: int = 0
    unclear: int = 0
    in_flight: int = 0
    strikes: int = 0
    # Workers currently allowed to probe: drops to one on a refusal and climbs
    # back a worker per `RECOVER_STREAK` answers, so a vendor that just refused
    # us is re-approached by a single request, not the whole pool at once.
    allowed: int = 0
    streak: int = 0
    cooling_until: float = 0.0
    failing: bool = False
    skipped: int = 0
    pace_s: float = 0.0  # spacing between probe starts; 0 = unpaced
    next_start: float = 0.0
    refused: dict[str, int] = field(default_factory=dict)  # token -> non-429 refusals
    refusing: set[str] = field(default_factory=set)  # refused since the last answer
    stamps: deque[float] = field(default_factory=deque)

    def state(self, now: float) -> str:
        if self.failing:
            return "failing"
        if self.done + self.skipped >= self.total:
            return "done"
        if self.cooling_until > now:
            return "cooling"
        return "running"

    def rate(self, now: float, window_s: float = RATE_WINDOW_S) -> float:
        while self.stamps and now - self.stamps[0] > RATE_WINDOW_S:
            self.stamps.popleft()
        return len(self.stamps) / window_s


ProbeFn = Callable[..., Awaitable[tuple[str, dict[str, Any]]]]


class SweepRunner:
    """One in-memory sweep at a time over the never-checked backlog."""

    def __init__(
        self,
        policy: Any,
        *,
        probe: ProbeFn | None = None,
        net_factory: Callable[[], Net] | None = None,
        cooldowns: tuple[float, ...] = COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._controls = HTMLControlCache()
        self._probe = probe or partial(validator.probe_token, controls=self._controls)
        self._net_factory = net_factory or (
            lambda: Net(
                user_agent=DEFAULT_USER_AGENT, timeout_s=15.0, min_interval_s=0.1, max_retries=2,
                connect_timeout_s=CONNECT_TIMEOUT_S,
            )
        )
        self._cooldowns = cooldowns
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._resume = asyncio.Event()
        self._resume.set()
        self._status = "idle"
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._started_clock = 0.0
        self._elapsed_s = 0.0
        self._limit: int | None = None
        self._ats: list[str] | None = None
        self._vendors: dict[str, _Vendor] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._error: str | None = None
        self._backlog: dict[str, int] | None = None
        self._backlog_at = 0.0

    # -- control ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, *, limit: int | None, ats: list[str] | None = None) -> dict[str, Any]:
        for name in ats or []:
            require_source_allowed(name)
        if self.running:
            raise SweepAlreadyRunning("A sweep is already running")
        pool = await self._policy.pool_or_none()
        if pool is None:
            raise RuntimeError("PostgreSQL is unavailable")
        sql = f"SELECT ats, token FROM token WHERE {_NEVER_CHECKED}"
        args: list[Any] = []
        if ats:
            sql += " AND ats = ANY($1::text[])"
            args.append(ats)
        else:
            sql += " AND NOT (ats = ANY($1::text[]))"
            args.append(sorted(SWEEP_SKIP))
        sql += " ORDER BY discovered_at NULLS LAST, ats, token"
        rows = [(row["ats"], row["token"]) for row in await pool.fetch(sql, *args)]
        queues = _round_robin(rows, limit)
        self._vendors = {
            vendor: _Vendor(
                queue=queue, width=width, allowed=width, total=len(queue),
                pace_s=SWEEP_PACE_S.get(vendor, 0.0),
            )
            for vendor, queue in queues.items()
            for width in (_width(vendor, queue[0]),)
        }
        self._limit, self._ats = limit, ats or None
        self._controls.clear()
        self._events.clear()
        if not ats:
            for skipped in sorted(SWEEP_SKIP):
                if not source_disabled(skipped):
                    self._event(skipped, "left out of all vendors · pick it to sweep it", "warn")
        self._error = None
        self._status = "running"
        self._started_at, self._finished_at = _now_iso(), None
        self._started_clock = self._clock()
        self._elapsed_s = 0.0
        self._resume.set()
        self._backlog = None
        self._task = asyncio.create_task(self._run(pool), name="token-sweep")
        return self.snapshot()

    def pause(self, paused: bool) -> dict[str, Any]:
        if self.running:
            if paused:
                self._resume.clear()
                self._status = "paused"
            else:
                self._resume.set()
                self._status = "running"
        return self.snapshot()

    async def cancel(self) -> dict[str, Any]:
        if self.running:
            assert self._task is not None
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            # Set here too: a task cancelled before its first step never
            # reaches `_run`'s handlers.
            self._status = "cancelled"
            self._finished_at = self._finished_at or _now_iso()
            self._elapsed_s = self._clock() - self._started_clock
            self._resume.set()
        return self.snapshot()

    async def stop(self) -> None:
        await self.cancel()

    # -- work ------------------------------------------------------------

    async def _run(self, pool: Any) -> None:
        try:
            async with self._net_factory() as net:
                await asyncio.gather(*(
                    self._worker(pool, net, name, vendor, slot)
                    for name, vendor in self._vendors.items()
                    for slot in range(vendor.width)
                ))
            self._status = "finished"
        except asyncio.CancelledError:
            self._status = "cancelled"
            raise
        except Exception as exc:  # surfaced on the page, never swallowed
            logger.exception("token sweep failed")
            self._error = f"{type(exc).__name__}: {exc}"
            self._status = "failed"
        finally:
            self._finished_at = _now_iso()
            self._elapsed_s = self._clock() - self._started_clock
            self._resume.set()

    async def _worker(self, pool: Any, net: Net, name: str, vendor: _Vendor, slot: int) -> None:
        request = partial(net_request, net, concurrency=vendor.width)
        while True:
            await self._resume.wait()
            if vendor.failing:
                return
            wait = vendor.cooling_until - self._clock()
            if wait > 0:
                await asyncio.sleep(min(wait, 5.0))
                continue
            if slot >= vendor.allowed and vendor.queue:
                # Parked until the vendor has answered enough to earn this slot.
                await asyncio.sleep(1.0)
                continue
            if not vendor.queue:
                if vendor.in_flight:
                    # A sibling's probe may yet be requeued by a 429.
                    await asyncio.sleep(0.5)
                    continue
                return
            token = vendor.queue.popleft()
            if vendor.pace_s:
                now = self._clock()
                start = max(now, vendor.next_start)
                vendor.next_start = start + vendor.pace_s
                if start > now:
                    await asyncio.sleep(start - now)
            vendor.in_flight += 1
            try:
                status, details = await self._probe(name, token, request=request)
            finally:
                vendor.in_flight -= 1
            if details.get("reason") in THROTTLED_REASONS:
                self._throttled(
                    name, vendor, token, details["reason"],
                    token_only=details.get("refusal_scope") == "token",
                )
                continue
            vendor.refusing.clear()
            vendor.streak += 1
            if vendor.streak >= RECOVER_STREAK:
                vendor.streak = 0
                if vendor.allowed < vendor.width:
                    vendor.allowed += 1
                if vendor.strikes:
                    vendor.strikes = 0
                    self._event(name, "recovered", "ok")
            checked_at = datetime.now(timezone.utc)
            await validator.persist_result(
                pool, name, token, status, checked_at,
                log=status == "live", config=details.get("config"),
            )
            if details.get("moved_to"):
                await validator.persist_moved(pool, name, token, details["moved_to"], checked_at)
            vendor.done += 1
            vendor.stamps.append(self._clock())
            if status == "live":
                vendor.live += 1
            elif status == "dead":
                vendor.dead += 1
            else:
                vendor.unclear += 1

    def _throttled(
        self, name: str, vendor: _Vendor, token: str, reason: str, *, token_only: bool = False,
    ) -> None:
        if reason == "transport" or token_only:
            # One host that would not connect, or an employer's own refusal
            # after a redirect: retry it later, then skip it, but leave the
            # vendor's pace and its other tokens alone.
            vendor.refused[token] = vendor.refused.get(token, 0) + 1
            if vendor.refused[token] >= MAX_TOKEN_REFUSALS:
                vendor.skipped += 1
                self._event(name, f"skipped {token} · {reason.removeprefix('http_')} twice", "warn")
            else:
                vendor.queue.append(token)
            return
        vendor.streak = 0
        if reason != "http_429":
            # A wall or a dead host may be this one tenant's, so the token goes
            # to the back -- the next probe asks someone else -- and is dropped
            # once it has refused us twice, instead of being retried first after
            # every cooldown until the vendor is given up.
            vendor.refused[token] = vendor.refused.get(token, 0) + 1
            vendor.refusing.add(token)
            if vendor.refused[token] >= MAX_TOKEN_REFUSALS:
                vendor.skipped += 1
                self._event(name, f"skipped {token} · {reason.removeprefix('http_')} twice", "warn")
            else:
                vendor.queue.append(token)
            if len(vendor.refusing) < REFUSALS_TO_COOL:
                return
        else:
            vendor.queue.appendleft(token)
        vendor.allowed = 1
        now = self._clock()
        if vendor.cooling_until > now:
            return  # a sibling already opened this window
        vendor.strikes += 1
        if vendor.strikes >= MAX_STRIKES:
            vendor.failing = True
            self._event(name, f"{reason.removeprefix('http_')} · gave up after {vendor.strikes} cooldowns", "err")
            return
        pause = self._cooldowns[min(vendor.strikes, len(self._cooldowns)) - 1]
        vendor.cooling_until = now + pause
        self._event(name, f"{reason.removeprefix('http_')} · cooling {round(pause / 60) or 1}m", "warn")

    def _event(self, vendor: str, text: str, tone: str) -> None:
        self._events.appendleft({"at": _now_iso(), "vendor": vendor, "text": text, "tone": tone})

    # -- read ------------------------------------------------------------

    async def backlog(self) -> dict[str, int] | None:
        """Never-checked tokens per vendor, cached briefly; None if degraded."""
        if self._backlog is not None and self._clock() - self._backlog_at < BACKLOG_TTL_S:
            return self._backlog
        pool = await self._policy.pool_or_none()
        if pool is None:
            return None
        rows = await pool.fetch(
            f"SELECT ats, count(*)::int AS n FROM token WHERE {_NEVER_CHECKED} GROUP BY ats ORDER BY n DESC"
        )
        self._backlog = {row["ats"]: row["n"] for row in rows if not source_disabled(row["ats"])}
        self._backlog_at = self._clock()
        return self._backlog

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        window_s = min(RATE_WINDOW_S, max(1.0, now - self._started_clock))
        vendors = []
        for name, vendor in self._vendors.items():
            state = vendor.state(now)
            vendors.append({
                "ats": name,
                "total": vendor.total,
                "done": vendor.done,
                "live": vendor.live,
                "dead": vendor.dead,
                "unclear": vendor.unclear,
                "skipped": vendor.skipped,
                "rate": round(vendor.rate(now, window_s), 2) if state == "running" else 0.0,
                "state": state,
                "cooling_s": max(0, round(vendor.cooling_until - now)) if state == "cooling" else 0,
                "strikes": vendor.strikes,
                "workers": vendor.allowed,
            })
        total = sum(v["total"] for v in vendors)
        done = sum(v["done"] for v in vendors)
        skipped = sum(v["skipped"] for v in vendors)
        rate = sum(v["rate"] for v in vendors)
        elapsed = (now - self._started_clock) if self.running else self._elapsed_s
        return {
            "status": self._status,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "elapsed_s": round(elapsed) if self._started_at else 0,
            "limit": self._limit,
            "ats": self._ats,
            "total": total,
            "done": done,
            "live": sum(v["live"] for v in vendors),
            "dead": sum(v["dead"] for v in vendors),
            "unclear": sum(v["unclear"] for v in vendors),
            "skipped": skipped,
            "rate": round(rate, 2),
            "eta_s": round((total - done - skipped) / rate) if rate > 0 and self._status == "running" else None,
            "vendors": vendors,
            "events": list(self._events),
            "error": self._error,
        }
