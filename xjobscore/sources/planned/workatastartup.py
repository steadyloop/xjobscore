"""YC Work at a Startup -- one authenticated company-list request.

``feed.config`` contains the captured request's ``headers`` and ``body``.
``items`` and ``id`` are optional response-field names (``companies`` and
``id`` by default). Credentials must never be committed.
"""

from __future__ import annotations

from xjobscore.net import Net
from xjobscore.records import Feed, Item, Record

URL = "https://www.workatastartup.com/companies/fetch"


async def harvest(feed: Feed, net: Net) -> Record:
    config = feed.config
    if "body" not in config or "headers" not in config:
        raise ValueError("workatastartup needs captured headers and body in feed.config")
    response = await net.post(URL, json=config["body"], headers=config["headers"])
    body = response.json()
    companies = body[config.get("items", "companies")]
    id_field = config.get("id", "id")
    return Record(
        items=[Item(str(company[id_field]), company) for company in companies],
        http_status=response.status,
    )
