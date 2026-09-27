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


def test_greenhouse_keys_are_case_insensitive_endpoint_identities():
    # `Duolingo` and `duolingo` are one board, so they must be one key.
    assert canonical_feed_key("greenhouse", "Duolingo") == "duolingo"
    assert canonical_feed_identity("greenhouse", "Acme") == ("greenhouse", "acme")


def test_case_insensitive_vendors_fold_to_one_key():
    # Each vendor here was checked against its live API: both spellings answer
    # with the same board, so they must not become two rows.
    assert canonical_feed_key("ashby", "Acorns") == "acorns"
    assert canonical_feed_key("smartrecruiters", "LinkedIn3") == "linkedin3"
    assert canonical_feed_key("oracle", "ewvl.fa.us8/CX_1") == "ewvl.fa.us8/cx_1"
    assert canonical_feed_key("bullhorn", "34/2WXP1S") == "34/2wxp1s"


def test_other_vendor_keys_keep_their_exact_spelling():
    assert canonical_feed_key("lever", "Acme") == "Acme"
    # comeet's key carries an API token, which is not safe to case-fold.
    assert (
        canonical_feed_key("comeet", "98.008/89833903C282AF80")
        == "98.008/89833903C282AF80"
    )


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
