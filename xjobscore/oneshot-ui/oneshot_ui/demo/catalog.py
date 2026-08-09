"""Offline catalog and adapter seam used only by demo mode.

This is a stand-in for :class:`oneshot_ui.gateway.SimpleCatalog`, which reaches
a hardcoded PostgreSQL DSN inside the FastAPI lifespan and therefore cannot
start without a database server.  Nothing here modifies or subclasses the real
gateway: it duck-types the same six methods the coordinator calls, exactly as
``tests/test_api.py``'s ``FixtureGateway`` already does.

Two properties matter beyond "it works offline":

* ``get_feed_ref`` resolves the sha256 reference produced by
  :func:`xjobscore.sources.catalog.feed_ref` — the identity ``board.py`` and the
  browser both use.  ``SimpleCatalog`` parses ``int(ref)`` instead, so its fetch
  and replay routes 404 for every reference the UI can actually send.
* ``parse`` reconstructs postings from stored ``Item`` payloads against durable
  bronze rather than a stub.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from typing import Any, Iterable

from xjobscore.records import Canonical, Fact, Feed, Item, Label, Posting, Record
from xjobscore.sources.catalog import feed_ref

from ..gateway import GatewayResult

__all__ = ["DemoCatalog", "DemoGateway", "posting_payload", "payload_posting"]


# Posting fields that are datetimes and therefore need ISO round-tripping when
# a posting crosses the bronze boundary as JSON.
_DATETIME_FIELDS = ("published_at", "source_updated_at", "deadline_at")


def posting_payload(posting: Posting) -> dict[str, Any]:
    """Render one posting as the verbatim JSON body a vendor would have sent."""
    payload = asdict(posting)
    for name in _DATETIME_FIELDS:
        value = payload.get(name)
        if isinstance(value, datetime):
            payload[name] = value.isoformat()
    return payload


def payload_posting(payload: dict[str, Any]) -> Posting:
    """Rebuild a posting from a stored payload, ignoring unknown keys."""
    known = {field.name for field in fields(Posting)}
    values = {key: value for key, value in payload.items() if key in known}
    for name in _DATETIME_FIELDS:
        value = values.get(name)
        if isinstance(value, str) and value:
            values[name] = datetime.fromisoformat(value)
    return Posting(**values)


class DemoCatalog:
    """In-process feed catalog. Never opens a socket."""

    def __init__(self, feeds: Iterable[Feed]) -> None:
        self._feeds = tuple(feeds)
        self._by_id = {feed.id: feed for feed in self._feeds}
        self._by_ref = {feed_ref(feed.source, feed.key): feed for feed in self._feeds}

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def all_feeds(self) -> list[Feed]:
        return list(self._feeds)

    async def get_feed(self, feed_id: int) -> Feed | None:
        return self._by_id.get(feed_id)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        """Resolve the browser-safe sha256 reference, then an integer ID."""
        feed = self._by_ref.get(ref)
        if feed is not None:
            return feed
        try:
            return self._by_id.get(int(ref))
        except (TypeError, ValueError):
            return None


class DemoGateway:
    """Serve a fixed corpus as if it had been harvested.

    ``round_index`` selects which snapshot of the corpus a fetch returns.  The
    seeder advances it to produce genuine second-round evidence: changed
    revisions, and a posting that vanishes from a complete/closing feed so the
    real lifecycle reconciler marks it unlisted.
    """

    def __init__(
        self,
        feeds: Iterable[Feed] | None = None,
        rounds: dict[int, tuple[tuple[Canonical, ...], ...]] | None = None,
    ) -> None:
        from .dataset import DEMO_FEEDS, demo_rounds

        self.feeds = tuple(feeds) if feeds is not None else DEMO_FEEDS
        self.rounds = rounds if rounds is not None else demo_rounds()
        self.catalog = DemoCatalog(self.feeds)
        self.round_index = 0
        self.fetch_calls = 0
        self.connected = False

    # -- catalog seam ----------------------------------------------------

    async def connect(self) -> None:
        await self.catalog.connect()
        self.connected = True

    async def disconnect(self) -> None:
        await self.catalog.disconnect()
        self.connected = False

    async def all_feeds(self) -> list[Feed]:
        return await self.catalog.all_feeds()

    async def get_feed(self, feed_id: int) -> Feed | None:
        return await self.catalog.get_feed(feed_id)

    async def get_feed_ref(self, ref: str) -> Feed | None:
        return await self.catalog.get_feed_ref(ref)

    # -- adapter seam ----------------------------------------------------

    @property
    def final_round(self) -> int:
        """The last round any feed defines."""
        return max((len(snapshots) for snapshots in self.rounds.values()), default=1) - 1

    def canonical_for(self, feed: Feed) -> tuple[Canonical, ...]:
        """Return the corpus this feed serves in the current round.

        A round the corpus does not define falls back to the last one it does,
        so repeated fetches after the seeder finishes stay stable instead of
        emptying a feed and closing everything on it.
        """
        snapshots = self.rounds.get(feed.id, ())
        if not snapshots:
            return ()
        return snapshots[min(self.round_index, len(snapshots) - 1)]

    async def fetch(self, feed: Feed) -> GatewayResult:
        from .dataset import FAILING_FEED_ERROR, FAILING_FEED_ID

        self.fetch_calls += 1
        if feed.id == FAILING_FEED_ID:
            # The coordinator turns this into a real 'error' poll with a
            # recorded message, which is what Source Coverage renders.
            raise RuntimeError(FAILING_FEED_ERROR)
        canonical = self.canonical_for(feed)
        postings = tuple(item for item in canonical if isinstance(item, Posting))
        record = Record(
            items=[
                Item(posting.external_id, posting_payload(posting), "json")
                for posting in postings
            ],
            envelope={
                "company": feed.company_name,
                "source": feed.source,
                "labels": [asdict(item) for item in canonical if isinstance(item, Label)],
                "facts": [
                    _fact_payload(item) for item in canonical if isinstance(item, Fact)
                ],
            },
            http_status=200,
            validators={"etag": f'"demo-{feed.source}-{feed.key}-r{self.round_index}"'},
        )
        return self.parse(feed, record)

    @staticmethod
    def parse(feed: Feed, record: Record) -> GatewayResult:
        """Rebuild canonical output from a captured record. No network, no clock."""
        canonical: list[Canonical] = []
        try:
            for item in record.items:
                if not isinstance(item.payload, dict):
                    raise TypeError(f"demo payloads are JSON objects, got {item.kind}")
                canonical.append(payload_posting(item.payload))
            for raw in record.envelope.get("labels", ()):
                canonical.append(Label(**raw))
            for raw in record.envelope.get("facts", ()):
                values = dict(raw)
                expires = values.get("expires_at")
                if isinstance(expires, str) and expires:
                    values["expires_at"] = datetime.fromisoformat(expires)
                canonical.append(Fact(**values))
        except Exception as exc:  # surfaced as parser_state='error', as a real parser would be
            return GatewayResult(feed, record, tuple(canonical), "error", str(exc))
        return GatewayResult(feed, record, tuple(canonical), "available")

    async def __aenter__(self) -> DemoGateway:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()


def _fact_payload(fact: Fact) -> dict[str, Any]:
    payload = asdict(fact)
    if isinstance(payload.get("expires_at"), datetime):
        payload["expires_at"] = payload["expires_at"].isoformat()
    return payload
