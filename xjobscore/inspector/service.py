"""Single-fetch orchestration with no ingestion side effects."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any

from xjobscore import router, sources
from xjobscore.sources import source_type
from xjobscore.sources.catalog import feed_ref
from xjobscore.inspector.cache import RunSnapshot, estimate_raw_size, estimate_size, now_utc
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.network import InspectorNet, telemetry
from xjobscore.net import NetError
from xjobscore.records import Feed


SOURCE_CATEGORIES = ("all", "ATS", "Web Board", "Other")


def display_source(source: str) -> str:
    """The source name shown by the inspector."""
    return source


def source_category(source: str) -> str:
    return source_type(source)


class InspectorService:
    def __init__(self, db: Any, *, user_agent: str, result_store: ResultStore) -> None:
        self.db = db
        self.user_agent = user_agent
        self.result_store = result_store
        self._locks: dict[int, Any] = {}

    async def get_result(self, feed_id: int) -> RunSnapshot | None:
        return await self.result_store.load_feed(feed_id)

    async def _feed_for_ref(self, ref: str) -> Feed | None:
        get_feed_ref = getattr(self.db, "get_feed_ref", None)
        if get_feed_ref is not None:
            return await get_feed_ref(ref)
        return next(
            (
                feed
                for feed in await self.db.all_feeds()
                if feed_ref(feed.source, feed.key) == ref
            ),
            None,
        )

    async def fetch_once_ref(self, ref: str, *, force_full: bool = True) -> RunSnapshot:
        feed = await self._feed_for_ref(ref)
        if feed is None:
            raise KeyError(ref)
        return await self.fetch_once(feed.id, force_full=force_full)

    async def get_result_ref(self, ref: str) -> RunSnapshot | None:
        feed = await self._feed_for_ref(ref)
        return await self.get_result(feed.id) if feed is not None else None

    async def source_groups(self) -> list[dict[str, Any]]:
        return await self.result_store.summary_groups()

    def _lock(self, feed_id: int):
        import asyncio

        return self._locks.setdefault(feed_id, asyncio.Lock())

    @staticmethod
    def _raw_projection(feed: Feed, external_id: str, kind: str, payload: Any) -> dict[str, Any]:
        volatile = router.VOLATILE.get(feed.source, frozenset())
        content_hash = router._content_hash(payload, kind, volatile)
        return {
            "feed_id": str(feed.id),
            "external_id": external_id,
            "kind": kind,
            "content_hash": content_hash,
            "payload_column": "payload_json" if kind == "json" else "payload_text",
            "envelope_column": "envelope",
        }

    async def fetch_once(self, feed_id: int, *, force_full: bool = True) -> RunSnapshot:
        async with self._lock(feed_id):
            feed = await self.db.get_feed(feed_id)
            if feed is None:
                raise KeyError(feed_id)
            if feed.source not in sources.SOURCES:
                raise sources.UnknownSource(feed.source)

            started = now_utc()
            started_mono = time.monotonic()
            status = "error"
            http_status: int | None = None
            truncated = False
            error_class: str | None = None
            error_detail: str | None = None
            items: list[dict[str, Any]] = []
            envelope: Any = {}
            raw_size_bytes = 0
            network = telemetry([])
            net: InspectorNet | None = None

            # A copied feed means force-full never mutates the real validator
            # state, and this process never calls the scheduler's poll path.
            fetch_feed = replace(feed, state={} if force_full else dict(feed.state))
            try:
                net = InspectorNet(user_agent=self.user_agent)
                async with net:
                    record = await sources.get(feed.source)(fetch_feed, net)
                network = telemetry(net.events)
                http_status = record.http_status
                truncated = record.truncated
                envelope = record.envelope
                items = [
                    {
                        "external_id": item.external_id,
                        "kind": item.kind,
                        "payload": item.payload,
                        "raw_document": self._raw_projection(
                            feed, item.external_id, item.kind, item.payload
                        ),
                    }
                    for item in record.items
                ]
                raw_size_bytes = sum(
                    estimate_raw_size(item.payload, item.kind) for item in record.items
                )
                if http_status == 304:
                    status = "not_modified"
                elif truncated:
                    status = "partial"
                else:
                    status = "ok"
            except Exception as exc:  # Inspector must return a useful result for failures.
                if net is not None:
                    network = telemetry(net.events)
                error_class = type(exc).__name__
                error_detail = str(exc)
                if isinstance(exc, NetError):
                    http_status = exc.status

            finished = now_utc()
            size_bytes = estimate_size({"items": items, "envelope": envelope, "network": network})
            snapshot = RunSnapshot(
                result_id=uuid.uuid4().hex,
                feed_id=feed.id,
                source=feed.source,
                key=feed.key,
                started_at=started,
                finished_at=finished,
                elapsed_ms=round((time.monotonic() - started_mono) * 1000, 1),
                status=status,
                http_status=http_status,
                item_count=len(items),
                raw_size_bytes=raw_size_bytes,
                truncated=truncated,
                force_full=force_full,
                error_class=error_class,
                error_detail=error_detail,
                items=tuple(items),
                envelope=envelope,
                size_bytes=size_bytes,
                network=network,
            )
            await self.result_store.save(snapshot)
            return snapshot

    async def feed_rows(self) -> list[dict[str, Any]]:
        feeds = await self.db.all_feeds()
        summaries = {
            int(result["feed_id"]): result
            for group in await self.source_groups()
            for result in group["results"]
        }
        rows = [
            {
                "feed_ref": feed_ref(feed.source, feed.key),
                "source": display_source(feed.source),
                "category": source_category(feed.source),
                "key": feed.key,
                "company_id": feed.company_id,
                "company_name": feed.company_name,
                "configured": True,
                "enabled": feed.enabled,
                "complete": feed.complete,
                "closes": feed.closes,
                "credentialed": feed.credentialed,
                "cadence_s": feed.cadence_s,
                "next_poll_at": feed.next_poll_at.isoformat() if feed.next_poll_at else None,
                "source_registered": feed.source in sources.SOURCES,
                "parser_registered": feed.source in sources.PARSERS,
                "last_result": summaries.get(feed.id),
            }
            for feed in feeds
        ]
        configured_sources = {display_source(feed.source) for feed in feeds}
        rows.extend(
            {
                # A registered source without a feed cannot be fetched: its
                # harvester needs the feed's source-specific key and config.
                "feed_ref": None,
                "source": source,
                "category": source_category(source),
                "key": "",
                "company_id": None,
                "company_name": None,
                "configured": False,
                "enabled": False,
                "complete": False,
                "closes": False,
                "credentialed": False,
                "cadence_s": None,
                "next_poll_at": None,
                "source_registered": True,
                "parser_registered": source in sources.PARSERS,
                "last_result": None,
            }
            for source in sorted(set(sources.SOURCES) - configured_sources)
        )
        return sorted(
            rows,
            key=lambda row: (row["source"], not row["configured"], row["key"], row["feed_ref"] or ""),
        )
