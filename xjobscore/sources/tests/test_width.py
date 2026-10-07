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
    "gem": 20,  # list.concurrency, shared jobs.gem.com
    "lever": 40,  # list.concurrency, shared api.lever.co
    "rippling": 8,  # list.concurrency, shared api.rippling.com
    "smartrecruiters": 16,  # list.concurrency, shared api.smartrecruiters.com
    "workday": 6,      # 24 // 4 page+detail fan-out
    "oracle": 12,      # 48 // 4 page+detail fan-out
    "eightfold": 2,    # 16 // 8
    "bamboohr": 2,     # 8 // 4
    **{
        name: 1
        for name in (
            'adp', 'amazon', 'apple', 'avature', 'breezy', 'bullhorn', 'comeet',
            'dayforce', 'flyio', 'fountain', 'hn', 'homerun', 'icims',
            'janestreet', 'jazzhr', 'jobvite', 'join', 'jsonld', 'manatal',
            'oleeo', 'peopleadmin', 'personio', 'pinpoint', 'recruitcrm',
            'recruitee', 'simplify', 'successfactors',
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
    assert sources.width("workday") == 12  # 48 // 4

    monkeypatch.delitem(net_module._VENDOR_CAPS, "myworkdayjobs.com")
    assert sources.width("workday") == 1  # uncapped is unmeasured, not unlimited


def test_width_reads_the_bespoke_adapters_own_constants(monkeypatch):
    assert taleo._CONCURRENCY == 12 and avature.CONCURRENCY == 5 and join.CONCURRENCY == 4
    monkeypatch.setattr(taleo, "_CONCURRENCY", 6)
    assert sources.width("taleo") == 2  # 12 // 6


def test_width_of_an_unregistered_source_is_one():
    """`poll` raising UnknownSource is where that failure belongs."""
    assert sources.width("no-such-source") == 1


# Workday's limiter counts per data center, so it runs one lane per data
# center with its own `_VENDOR_RATES` budget plus one shared `other` lane.

def test_workday_lanes_follow_the_rate_table():
    assert sources.partitions("workday") == ("wd1", "wd3", "wd5")
    assert sources.lanes("workday") == ["workday:wd1", "workday:wd3", "workday:wd5", "workday:other"]
    assert sources.lanes("greenhouse") == ["greenhouse"]
    assert sources.partitions("greenhouse") == ()


def test_a_new_data_center_budget_adds_its_lane(monkeypatch):
    monkeypatch.setitem(net_module._VENDOR_RATES, "wd12.myworkdayjobs.com", (5.0, 20))
    assert "workday:wd12" in sources.lanes("workday")


def test_each_workday_lane_is_sized_by_its_own_cap():
    assert {lane: sources.width(lane) for lane in sources.lanes("workday")} == {
        "workday:wd1": 6,    # 24 // 4
        "workday:wd3": 4,    # 16 // 4
        "workday:wd5": 6,    # 24 // 4
        "workday:other": 6,  # shared myworkdayjobs.com cap, 24 // 4
    }


def test_lane_of_a_feed_key():
    assert sources.lane_of("workday", "acme/wd1/external") == "workday:wd1"
    assert sources.lane_of("workday", "acme/WD3/external") == "workday:wd3"
    assert sources.lane_of("workday", "acme/wd103/external") == "workday:other"
    assert sources.lane_of("greenhouse", "acme") == "greenhouse"


def test_lane_filter_is_what_the_claim_narrows_by():
    assert sources.lane_filter("lever") == ("lever", None)
    assert sources.lane_filter("workday:wd3") == ("workday", (2, ("wd3",), False))
    assert sources.lane_filter("workday:other") == ("workday", (2, ("wd1", "wd3", "wd5"), True))
