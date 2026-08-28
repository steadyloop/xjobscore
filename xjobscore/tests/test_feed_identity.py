import pytest

from xjobscore import store
from xjobscore.feed_identity import (
    canonical_feed_identity,
    canonical_feed_key,
    canonical_feed_source,
)


def test_interim_workday_list_source_is_an_alias_for_workday():
    assert canonical_feed_source("workday-list") == "workday"
    assert canonical_feed_identity("workday", "Cisco/wd5/Cisco_Careers") == (
        "workday",
        "cisco/wd5/cisco_careers",
    )


def test_workday_keys_are_case_insensitive_endpoint_identities():
    assert (
        canonical_feed_key("workday", "Cisco/wd5/Cisco_Careers")
        == "cisco/wd5/cisco_careers"
    )
    assert canonical_feed_identity("workday", "Acme/wd1/Jobs") == (
        "workday",
        "acme/wd1/jobs",
    )


def test_other_vendor_keys_keep_their_exact_spelling():
    assert canonical_feed_key("greenhouse", "Acme") == "Acme"


def test_workday_posting_namespace_uses_the_canonical_feed_identity():
    assert store.source_instance(
        store.Feed(id=1, source="workday", key="Acme/wd1/Careers")
    ) == "workday:acme/wd1/careers"


@pytest.mark.asyncio
async def test_bulk_feed_import_normalizes_before_deduplicating():
    class Connection:
        def __init__(self):
            self.args = None

        async def fetch(self, query, *args):
            self.args = args
            return [{"inserted": True}]

    connection = Connection()
    result = await store.import_feeds(
        connection,
        [
            ("workday", "Cisco/wd5/Cisco_Careers"),
            ("workday", "cisco/wd5/cisco_careers"),
        ],
        enabled=True,
    )

    assert result == (1, 0)
    assert connection.args == (
        ["workday"],
        ["cisco/wd5/cisco_careers"],
        True,
    )


@pytest.mark.asyncio
async def test_feed_import_preserves_existing_polling_state():
    class Connection:
        async def fetchval(self, query, *args):
            assert "ON CONFLICT (source, key) DO UPDATE SET source" in query
            assert "SET polling" not in query
            assert args == ("greenhouse", "acme", True)
            return 17

    feed_id = await store.import_feed(
        Connection(), source="greenhouse", key="acme", enabled=True
    )

    assert feed_id == 17
