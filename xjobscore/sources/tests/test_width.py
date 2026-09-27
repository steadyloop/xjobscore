"""`sources.width` -- the per-vendor pool size run.cycle spawns workers from.

Pinned exhaustively rather than sampled: the number is derived from four
different places (a source's `list`/`page`/`detail` concurrency, docxtract's
`detail_concurrency`, a bespoke module constant, and `net._VENDOR_CAPS`), so a
config edit anywhere can move a width without touching this package. A pinned
table turns that into a failing test naming the source that moved.
"""

from __future__ import annotations

from xjobscore import net as net_module
from xjobscore import sources
from xjobscore.sources import avature, join, taleo

# Shared-host vendors take their list leg's width; per-tenant vendors take
# their vendor cap divided by one poll's own fan-out; everything unmeasured
# is 1, because a shared host with no declared width is on net's serialized
# 1 req/s path and a per-tenant host with no vendor cap has no known ceiling.
EXPECTED = {
    "ashby": 44,       # list.concurrency, shared api.ashbyhq.com
    "greenhouse": 30,  # list.concurrency, shared boards-api.greenhouse.io
    "workday": 2,      # 24 // 10 page+detail fan-out
    "eightfold": 2,    # 16 // 8
    "bamboohr": 2,     # 8 // 4
    **{
        name: 1
        for name in (
            'adp', 'amazon', 'apple', 'avature', 'breezy', 'bullhorn', 'comeet',
            'dayforce', 'flyio', 'fountain', 'gem', 'hn', 'homerun', 'icims',
            'janestreet', 'jazzhr', 'jobvite', 'join', 'jsonld', 'lever', 'manatal',
            'oleeo', 'oracle', 'peopleadmin', 'personio', 'pinpoint', 'recruitcrm',
            'recruitee', 'rippling', 'simplify', 'smartrecruiters', 'successfactors',
            'talentreef', 'taleo', 'teamtailor', 'trakstar', 'ukg', 'unstop',
            'usajobs', 'vincere', 'workable', 'workatastartup', 'zohorecruit',
        )
    },
}


def test_every_registered_source_has_a_pinned_width():
    """A newly registered source must be sized here, not silently default."""
    assert set(EXPECTED) == set(sources.SOURCES)
    assert {name: sources.width(name) for name in sources.SOURCES} == EXPECTED


def test_width_reads_the_list_concurrency_of_a_shared_host_vendor(monkeypatch):
    monkeypatch.setitem(sources.json_api.CONFIGS["greenhouse"]["list"], "concurrency", 7)
    assert sources.width("greenhouse") == 7


def test_width_reads_the_vendor_cap_of_a_per_tenant_vendor(monkeypatch):
    """Workday's hosts are per tenant, so `_VENDOR_CAPS` is the only ceiling."""
    monkeypatch.setitem(net_module._VENDOR_CAPS, "myworkdayjobs.com", 48)
    assert sources.width("workday") == 4  # 48 // 10

    monkeypatch.delitem(net_module._VENDOR_CAPS, "myworkdayjobs.com")
    assert sources.width("workday") == 1  # uncapped is unmeasured, not unlimited


def test_width_reads_the_bespoke_adapters_own_constants(monkeypatch):
    assert taleo._CONCURRENCY == 12 and avature.CONCURRENCY == 5 and join.CONCURRENCY == 4
    monkeypatch.setattr(taleo, "_CONCURRENCY", 6)
    assert sources.width("taleo") == 2  # 12 // 6


def test_width_of_an_unregistered_source_is_one():
    """`poll` raising UnknownSource is where that failure belongs."""
    assert sources.width("no-such-source") == 1
