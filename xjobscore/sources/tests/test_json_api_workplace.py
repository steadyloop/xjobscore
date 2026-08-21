"""Structured workplace_type_raw wiring added on top of previously-unused
`extra` fields (recon: silver-stage remote/workplace detection, 2026-08).

`breezy`, `workable`, and `bamboohr` had no test coverage at all before this
change despite shipping fixtures; `recruitee` is covered by
`test_json_api_boards.py` but that file never asserted on workplace. All four
sources carry a boolean signal (`is_remote` / `telecommuting` / `remote` +
`hybrid` + `on_site` / `isRemote`) that was previously captured into
`source_extra` and never read.
"""

from __future__ import annotations

import copy

import pytest

from xjobscore.records import Feed, Item
from xjobscore.sources import json_api

from test_json_api_boards import FixtureNet, fixture


def feed_for(name: str, key: str) -> Feed:
    return Feed(id=1, source=name, key=key, config={})


async def harvest_first(name: str, key: str, payload: dict):
    net = FixtureNet(payload)
    feed = feed_for(name, key)
    record = await json_api.harvest(name, feed, net)
    return json_api.parse(name, feed, record.items[0], record.envelope)[0]


async def test_breezy_maps_is_remote_to_workplace_type_raw():
    posting = await harvest_first("breezy", "new-incentives", fixture("breezy"))
    assert posting.workplace_type_raw is None  # fixture's first row is is_remote: false

    remote_payload = copy.deepcopy(fixture("breezy"))
    remote_payload[0]["location"]["is_remote"] = True
    posting = await harvest_first("breezy", "new-incentives", remote_payload)
    assert posting.workplace_type_raw == "remote"


async def test_workable_maps_telecommuting_to_workplace_type_raw():
    posting = await harvest_first("workable", "huggingface", fixture("workable"))
    assert posting.workplace_type_raw == "remote"  # fixture's first row is telecommuting: true


async def test_recruitee_maps_on_site_flag_to_workplace_type_raw():
    posting = await harvest_first("recruitee", "transperfect", fixture("recruitee"))
    assert posting.workplace_type_raw == "on_site"  # fixture's first row is on_site: true


def test_bamboohr_maps_is_remote_to_workplace_type_raw():
    feed = feed_for("bamboohr", "prentkeromich")

    absent = json_api.parse("bamboohr", feed, Item("474", {"isRemote": None}), {})[0]
    assert absent.workplace_type_raw is None

    remote = json_api.parse("bamboohr", feed, Item("474", {"isRemote": True}), {})[0]
    assert remote.workplace_type_raw == "remote"
