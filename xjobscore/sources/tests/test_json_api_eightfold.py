"""Eightfold links retain the same board identity used to fetch the listing."""

from urllib.parse import parse_qs, urlsplit

import pytest

from xjobscore.records import Feed, Item
from xjobscore.sources import json_api


@pytest.mark.parametrize("key,config,host,domain", [
    ("app.eightfold.ai/sephora.com", {}, "app.eightfold.ai", "sephora.com"),
    ("microsoft", {"host": "apply.careers.microsoft.com", "domain": "microsoft.com"},
     "apply.careers.microsoft.com", "microsoft.com"),
    ("app.eightfold.ai/old.example", {"domain": "sephora.com"},
     "app.eightfold.ai", "sephora.com"),
])
def test_posting_and_apply_links_identify_the_board(key, config, host, domain):
    feed = Feed(id=1, source="eightfold", key=key, config=config)
    item = Item(external_id="790318857421", payload={
        "name": "Intern, Applied AI & Automation",
        "positionUrl": "/careers/job/790318857421",
    })
    posting = json_api.parse("eightfold", feed, item, {})[0]
    expected = f"https://{host}/careers/job/790318857421?domain={domain}"
    assert posting.url_posting == expected
    assert posting.apply_url == expected


@pytest.mark.parametrize("path", [
    "/careers/job/private?pid=563602808841765",
    "/careers/job/private?pid=563602808841765&domain=wrong.example#details",
    "/careers/job/private?pid=563602808841765&domain=sephora.com#details",
])
def test_existing_query_and_fragment_survive_domain_merge(path):
    feed = Feed(id=1, source="eightfold", key="app.eightfold.ai/sephora.com")
    item = Item(external_id="563602808841765", payload={
        "name": "Intern", "positionUrl": path,
    })
    posting = json_api.parse("eightfold", feed, item, {})[0]
    for url in (posting.url_posting, posting.apply_url):
        parsed = urlsplit(url)
        assert parsed.path == "/careers/job/private"
        assert parse_qs(parsed.query) == {"pid": ["563602808841765"], "domain": ["sephora.com"]}
        assert parsed.fragment == urlsplit(path).fragment
