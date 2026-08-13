"""Single-fetch orchestration with no ingestion side effects."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from xjobscore import router, sources
from xjobscore.sources import json_api, source_type
from xjobscore.inspector import parsing
from xjobscore.inspector.cache import RunSnapshot, estimate_raw_size, estimate_size, now_utc
from xjobscore.inspector.db import feed_ref
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.network import InspectorNet, StageTimer, telemetry
from xjobscore.inspector.profile import source_profile
from xjobscore.net import NetError
from xjobscore.records import Feed, Item, Record


SOURCE_CATEGORIES = ("all", "ATS", "Direct", "Board", "Other")
# Generic extraction strategies are implementation details, not inspector
# sources a user can select or fetch directly.
HIDDEN_SOURCES = frozenset({"jsonld"})
SMARTRECRUITERS_HOST = "api.smartrecruiters.com"
SMARTRECRUITERS_CONCURRENCY = 8
WORKDAY_PAGE_CONCURRENCY = 32
WORKDAY_DETAIL_CONCURRENCY = 16


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
        self._shared_host_semaphores = {
            SMARTRECRUITERS_HOST: asyncio.Semaphore(SMARTRECRUITERS_CONCURRENCY)
        }

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

    async def fetch_once_ref(
        self,
        ref: str,
        *,
        force_full: bool = True,
        trigger: str = "manual",
        etag: str | None = None,
    ) -> RunSnapshot:
        feed = await self._feed_for_ref(ref)
        if feed is None:
            raise KeyError(ref)
        return await self.fetch_once(
            feed.id, force_full=force_full, trigger=trigger, etag=etag
        )

    async def get_result_ref(self, ref: str) -> RunSnapshot | None:
        return await self.result_store.load_ref(ref)

    async def source_groups(self) -> list[dict[str, Any]]:
        return await self.result_store.summary_groups()

    def _lock(self, feed_id: int):
        import asyncio

        return self._locks.setdefault(feed_id, asyncio.Lock())

    @staticmethod
    def _workday_detail_path(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        info = payload.get("jobPostingInfo")
        external_url = info.get("externalUrl") if isinstance(info, dict) else None
        if not external_url:
            return None
        path = urlsplit(str(external_url)).path
        marker = path.find("/job/")
        return path[marker:] if marker >= 0 else None

    @staticmethod
    def _workday_host(feed: Feed) -> str | None:
        parts = feed.key.split("/", 2)
        return f"{parts[0]}.{parts[1]}.myworkdayjobs.com" if len(parts) == 3 else None

    def _parse_with_alias_scope(
        self, feed: Feed, items: Sequence[Item], envelope: dict
    ) -> dict[str, Any]:
        """Run parse_record with one alias-lookup connection for the pass.

        Called via asyncio.to_thread, so this whole call -- including every
        alias_lookup_scope lookup parse_record triggers -- runs start to
        finish on one worker thread. That is what makes it safe for
        alias_lookup_scope to hand out a callable backed by a raw sqlite3
        connection: the connection never crosses threads.
        """
        with self.result_store.alias_lookup_scope() as resolve_alias:
            return parsing.parse_record(feed, items, envelope, resolve_alias)

    @staticmethod
    def _raw_projection(feed: Feed, external_id: str, kind: str, payload: Any) -> dict[str, Any]:
        return {
            "feed_id": str(feed.id),
            "external_id": external_id,
            "kind": kind,
            "content_hash": None,
            "payload_column": "payload_json" if kind == "json" else "payload_text",
            "envelope_column": "envelope",
        }

    async def fetch_once(
        self,
        feed_id: int,
        *,
        force_full: bool = True,
        trigger: str = "manual",
        etag: str | None = None,
    ) -> RunSnapshot:
        async with self._lock(feed_id):
            feed = await self.db.get_feed(feed_id)
            if feed is None:
                raise KeyError(feed_id)
            if feed.source not in sources.SOURCES:
                raise sources.UnknownSource(feed.source)

            started = now_utc()
            started_mono = time.monotonic()
            stages = StageTimer()
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
            parse_outcome: dict[str, Any] | None = None
            response_etag: str | None = None
            reused_item_count = 0
            record: Record | None = None

            # A copied feed means force-full never mutates the real ETag, and
            # this process never calls the scheduler's poll path. SmartRecruiters
            # additionally requires a complete local payload before any validator
            # is safe to send.
            ref = feed_ref(feed.source, feed.key)
            stored = (
                await self.result_store.load_ref(ref)
                if not force_full and feed.source == "smartrecruiters"
                else None
            )
            workday_stored_items = (
                await self.result_store.load_latest_items_ref(ref)
                if not force_full and feed.source == "workday"
                else ()
            )
            validator = etag
            if not force_full and feed.source == "smartrecruiters":
                # A partial SmartRecruiters run may contain a useful prefix for
                # revision reuse, but it cannot satisfy a whole-list 304. Only
                # send a validator when the stored snapshot is complete.
                stored_complete = (
                    stored is not None
                    and stored.status in {"ok", "not_modified"}
                    and not stored.truncated
                    and bool(stored.items)
                )
                if stored_complete:
                    validator = stored.response_etag or validator or feed.etag
                else:
                    validator = None
            elif validator is None and not force_full:
                # Only offer a validator we can honor: a 304 returns no body, so
                # revalidating is worth it only while the run that carries the
                # matching payload is still the one holding item detail.
                stored_etag, stored_items = await self.result_store.latest_validator(ref)
                validator = stored_etag if stored_items else None
                if validator is None:
                    validator = feed.etag
            fetch_feed = replace(
                feed, etag=validator if validator is not None else (None if force_full else feed.etag)
            )
            try:
                if feed.source == "workday" and (host := self._workday_host(feed)):
                    self._shared_host_semaphores.setdefault(
                        host, asyncio.Semaphore(WORKDAY_PAGE_CONCURRENCY)
                    )
                net = InspectorNet(
                    user_agent=self.user_agent,
                    shared_host_semaphores=self._shared_host_semaphores,
                )
                async with net:
                    if feed.source in {"smartrecruiters", "workday"}:
                        prior_items = (
                            {
                                (
                                    self._workday_detail_path(item["payload"])
                                    if feed.source == "workday"
                                    else item["external_id"]
                                ): Item(
                                    external_id=item["external_id"],
                                    payload=item["payload"],
                                    kind=item["kind"],
                                )
                                for item in (
                                    workday_stored_items
                                    if feed.source == "workday"
                                    else stored.items
                                )
                                if feed.source != "workday"
                                or self._workday_detail_path(item["payload"]) is not None
                            }
                            if (
                                bool(workday_stored_items)
                                if feed.source == "workday"
                                else stored is not None and bool(stored.items)
                            )
                            else None
                        )
                        harvest_options = (
                            json_api.JsonApiHarvestOptions(
                                page_concurrency=WORKDAY_PAGE_CONCURRENCY,
                                detail_concurrency=WORKDAY_DETAIL_CONCURRENCY,
                                list_revision_fields=("title", "bulletFields.0"),
                                detail_revision_fields=(
                                    "jobPostingInfo.title",
                                    "jobPostingInfo.jobReqId",
                                ),
                                prior_items=prior_items,
                            )
                            if feed.source == "workday"
                            else json_api.JsonApiHarvestOptions(
                                page_step=100,
                                page_concurrency=SMARTRECRUITERS_CONCURRENCY,
                                detail_concurrency=SMARTRECRUITERS_CONCURRENCY,
                                revision_fields=("jobAdId", "releasedDate"),
                                prior_items=prior_items,
                            )
                        )
                        record = await json_api.harvest(
                            feed.source,
                            fetch_feed,
                            net,
                            options=harvest_options,
                        )
                    else:
                        record = await sources.get(feed.source)(fetch_feed, net)
                stages.record("harvest")
                network = telemetry(net.events)
                reused_item_count = record.reused_item_count
                http_status = record.http_status
                truncated = record.truncated
                envelope = record.envelope
                response_etag = record.etag
                items = []
                item_raw_sizes = []
                for item in record.items:
                    item_dict = {
                        "external_id": item.external_id,
                        "kind": item.kind,
                        "payload": item.payload,
                        "raw_document": self._raw_projection(
                            feed, item.external_id, item.kind, item.payload
                        ),
                    }
                    if item.kind == "json":
                        payload_json = json.dumps(
                            item.payload, ensure_ascii=False, separators=(",", ":"), default=str
                        )
                        item_dict["payload_json"] = payload_json
                        item_raw_sizes.append(len(payload_json.encode("utf-8")))
                    else:
                        item_raw_sizes.append(estimate_raw_size(item.payload, item.kind))
                    items.append(item_dict)
                raw_size_bytes = sum(item_raw_sizes)
                stages.record("project")
                if http_status == 304:
                    status = "not_modified"
                elif truncated:
                    status = "partial"
                else:
                    status = "ok"
                if status == "not_modified":
                    # The validator matched, so the stored payload is the same
                    # bytes this request would have downloaded. Carrying it
                    # forward keeps the run displayable and its detail rows from
                    # being trimmed away behind an empty run.
                    if stored is None:
                        stored = await self.result_store.load_ref(ref)
                    if stored is not None and stored.items:
                        items = list(stored.items)
                        envelope = stored.envelope
                        raw_size_bytes = stored.raw_size_bytes
                        reused_item_count = len(items)
                        parse_outcome = {
                            "status": stored.parse_status,
                            "summary": stored.parse_summary,
                            "items": list(stored.parse_items),
                        }
                    # A 304 usually omits the validator that produced it.
                    response_etag = response_etag or validator
                    stages.record("hydrate")
                if status in ("ok", "partial"):
                    parse_outcome = await asyncio.to_thread(
                        self._parse_with_alias_scope, feed, record.items, record.envelope
                    )
                stages.record("parse")
            except Exception as exc:  # Inspector must return a useful result for failures.
                if net is not None:
                    network = telemetry(net.events)
                error_class = type(exc).__name__
                error_detail = str(exc)
                if isinstance(exc, NetError):
                    http_status = exc.status
                stages.record("failed")

            finished = now_utc()
            network["summary"]["reused_item_count"] = reused_item_count
            if record is not None:
                network["summary"].update(
                    enumerated_count=record.enumerated_item_count,
                    partition_count=record.partition_count,
                    detail_fetched_count=record.detail_fetched_count,
                    result_window_truncated=record.result_window_truncated,
                )
            size_bytes = raw_size_bytes + estimate_size(envelope) + estimate_size(network)
            stages.record("serialize")
            # Attached last so the breakdown covers every stage above it. The
            # size estimate above therefore misses this key by a few hundred
            # bytes, which is within what "estimate" already promises.
            network = {**network, "stages": stages.stages()}
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
                trigger=trigger,
                parse_status=parse_outcome["status"] if parse_outcome else "skipped",
                parse_summary=parse_outcome["summary"] if parse_outcome else None,
                parse_items=tuple(parse_outcome["items"]) if parse_outcome else (),
                response_etag=response_etag,
            )
            await self.result_store.save(snapshot)
            return snapshot

    async def feed_rows(self) -> list[dict[str, Any]]:
        feeds = [
            feed for feed in await self.db.all_feeds()
            if feed.source not in HIDDEN_SOURCES
        ]
        descriptions = await self.result_store.company_descriptions(
            {feed.company_id for feed in feeds if feed.company_id is not None}
        )
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
                "profile": source_profile(feed.source),
                "key": feed.key,
                "company_id": feed.company_id,
                "company_name": feed.company_name,
                "company_description": descriptions.get(feed.company_id),
                "configured": True,
                "enabled": feed.enabled,
                "source_registered": feed.source in sources.SOURCES,
                "parser_registered": feed.source in sources.PARSERS,
                "last_result": (
                    {k: v for k, v in summaries.get(feed.id, {}).items()
                     if k in ("status", "item_count", "elapsed_ms", "finished_at", "error_class", "error_detail")}
                    if summaries.get(feed.id)
                    else None
                ),
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
                "profile": source_profile(source),
                "key": "",
                "company_id": None,
                "company_name": None,
                "company_description": None,
                "configured": False,
                "enabled": False,
                "source_registered": True,
                "parser_registered": source in sources.PARSERS,
                "last_result": None,
            }
            for source in sorted(
                set(sources.SOURCES) - configured_sources - HIDDEN_SOURCES
            )
        )
        return sorted(
            rows,
            key=lambda row: (row["source"], not row["configured"], row["key"], row["feed_ref"] or ""),
        )
