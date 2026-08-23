"""Single-fetch orchestration with no ingestion side effects."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from xjobscore import router, sources, store as core_store
from xjobscore.sources import json_api
from xjobscore.inspector import parsing
from xjobscore.inspector.cache import RunSnapshot, estimate_raw_size, estimate_size, now_utc
from xjobscore.inspector.db import feed_ref
from xjobscore.inspector.persistence import ResultStore
from xjobscore.inspector.network import InspectorNet, StageTimer, telemetry
from xjobscore.inspector.profile import source_profile
from xjobscore.net import NetError
from xjobscore.records import Feed, Item, Record


SOURCE_CATEGORIES = ("all", "ATS", "Direct", "Board")
# Generic extraction strategies are implementation details, not inspector
# sources a user can select or fetch directly.
HIDDEN_SOURCES = frozenset({"jsonld"})
SMARTRECRUITERS_HOST = "api.smartrecruiters.com"
ASHBY_HOST = "api.ashbyhq.com"


def _detail_concurrency(source: str) -> int:
    """The source's own configured detail width.

    Read from the config rather than restated here: a second copy of a width
    is how the inspector drifted into measuring 8 while production ran 4.
    """
    return json_api.CONFIGS[source].get("detail", {}).get("concurrency", 1)


def _list_concurrency(source: str) -> int:
    """The source's own configured list width (Ashby has no detail stage)."""
    return json_api.CONFIGS[source].get("list", {}).get("concurrency", 1)


def display_source(source: str) -> str:
    """The source name shown by the inspector."""
    return source


class InspectorService:
    def __init__(
        self,
        db: Any,
        *,
        user_agent: str,
        result_store: ResultStore,
        company_store: Any | None = None,
    ) -> None:
        self.db = db
        self.user_agent = user_agent
        self.result_store = result_store
        self.company_store = company_store
        self._locks: dict[int, Any] = {}
        # Single-slot parse cache: (result_id, outcome) for the feed pane being
        # viewed. Parse previews are recomputed on demand, so retaining more
        # than the current pane trades memory for nothing.
        self._parse_slot: tuple[str, dict[str, Any]] | None = None
        # Every SmartRecruiters/Ashby feed shares one host, but InspectorService
        # builds a fresh InspectorNet per feed, so Net's own per-host semaphore
        # would hand each concurrent fetch its own allowance. Width comes from
        # the config so this stays a binding, not a second opinion.
        self._shared_host_semaphores = {
            SMARTRECRUITERS_HOST: asyncio.Semaphore(_detail_concurrency("smartrecruiters")),
            ASHBY_HOST: asyncio.Semaphore(_list_concurrency("ashby")),
        }
        # Rate-limit adaptation outlives the per-fetch InspectorNet: a host
        # that 429ed a minute ago is still throttled for the next run.
        self._shared_host_paces: dict[str, Any] = {}
        # Net._VENDOR_CAPS semaphores, keyed by suffix. Process-scoped for the
        # same reason as _shared_host_semaphores: InspectorService builds a
        # fresh InspectorNet per feed, so a vendor-wide cap (e.g. Workday's
        # myworkdayjobs.com, spanning many tenant hostnames) only binds if
        # concurrent fetches of that vendor share one semaphore instance.
        self._shared_vendor_semaphores: dict[str, Any] = {}

    async def get_result(self, feed_id: int) -> RunSnapshot | None:
        snapshot = await self.result_store.load_feed(feed_id)
        if snapshot is None:
            return None
        feed = await self.db.get_feed(feed_id)
        return await self._with_parse(snapshot, feed)

    async def _with_parse(
        self, snapshot: RunSnapshot, feed: Feed | None
    ) -> RunSnapshot:
        """Derive the parse preview from the staged payload, caching in memory."""
        if feed is None:
            return snapshot
        slot = self._parse_slot
        outcome = slot[1] if slot is not None and slot[0] == snapshot.result_id else None
        if outcome is None:
            staged_items = tuple(
                Item(
                    external_id=item["external_id"],
                    payload=item["payload"],
                    kind=item["kind"],
                )
                for item in snapshot.items
            )
            aliases = await self._company_alias_index()
            outcome = await asyncio.to_thread(
                self._parse_with_aliases,
                feed,
                staged_items,
                snapshot.envelope,
                aliases,
            )
            self._parse_slot = (snapshot.result_id, outcome)
        return replace(
            snapshot,
            parse_status=outcome["status"],
            parse_summary=outcome["summary"],
            parse_items=tuple(outcome["items"]),
        )

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
        trigger: str = "manual",
        etag: str | None = None,
    ) -> RunSnapshot:
        feed = await self._feed_for_ref(ref)
        if feed is None:
            raise KeyError(ref)
        return await self.fetch_once(feed.id, trigger=trigger, etag=etag)

    async def get_result_ref(self, ref: str) -> RunSnapshot | None:
        feed = await self._feed_for_ref(ref)
        if feed is None:
            return None
        snapshot = await self.result_store.load_ref(feed.source, feed.key)
        if snapshot is None:
            return None
        return await self._with_parse(snapshot, feed)

    async def source_groups(self) -> list[dict[str, Any]]:
        return await self.result_store.summary_groups()

    def _lock(self, feed_id: int):
        import asyncio

        return self._locks.setdefault(feed_id, asyncio.Lock())

    def _parse_with_aliases(
        self,
        feed: Feed,
        items: Sequence[Item],
        envelope: dict,
        aliases: dict[str, tuple[int, str]],
    ) -> dict[str, Any]:
        """Run parse_record against the PostgreSQL alias snapshot."""
        memo: dict[str, tuple[int, str] | None] = {}

        def resolve_alias(raw: str) -> tuple[int, str] | None:
            if raw not in memo:
                memo[raw] = next(
                    (
                        aliases[key]
                        for key in core_store.alias_candidates(raw)
                        if key in aliases
                    ),
                    None,
                )
            return memo[raw]

        return parsing.parse_record(feed, items, envelope, resolve_alias)

    async def _company_alias_index(self) -> dict[str, tuple[int, str]]:
        read = getattr(self.company_store, "company_alias_index", None)
        return await read() if read is not None else {}

    async def _company_descriptions(self, company_ids: set[int]) -> dict[int, str]:
        read = getattr(self.company_store, "company_descriptions", None)
        return await read(company_ids) if read is not None else {}

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
            response_etag: str | None = None
            reused_item_count = 0
            record: Record | None = None

            # A copied feed means this process never mutates the real ETag or
            # calls the scheduler's poll path. SmartRecruiters additionally
            # requires a complete local payload before any validator is safe
            # to send.
            stored = (
                await self.result_store.load_ref(feed.source, feed.key)
                if feed.source == "smartrecruiters"
                else None
            )
            validator = etag
            if feed.source == "smartrecruiters":
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
            elif validator is None:
                # Only offer a validator we can honor: a 304 returns no body, so
                # revalidating is worth it only while the run that carries the
                # matching payload is still the one holding item detail.
                stored_etag, stored_items = await self.result_store.latest_validator(
                    feed.source, feed.key
                )
                validator = stored_etag if stored_items else None
                if validator is None:
                    validator = feed.etag
            fetch_feed = replace(
                feed, etag=validator if validator is not None else feed.etag
            )
            try:
                # Workday needs no per-tenant entry here: Net's vendor cap
                # covers myworkdayjobs.com across every tenant hostname, which
                # is what this bespoke semaphore was approximating.
                net = InspectorNet(
                    user_agent=self.user_agent,
                    shared_host_semaphores=self._shared_host_semaphores,
                    shared_host_paces=self._shared_host_paces,
                    shared_vendor_semaphores=self._shared_vendor_semaphores,
                )
                async with net:
                    if feed.source == "smartrecruiters":
                        prior_items = (
                            {
                                item["external_id"]: Item(
                                    external_id=item["external_id"],
                                    payload=item["payload"],
                                    kind=item["kind"],
                                )
                                for item in stored.items
                            }
                            if stored is not None and bool(stored.items)
                            else None
                        )
                        # Deliberately no page_concurrency/detail_concurrency
                        # here: widths belong to the config, so the inspector
                        # measures exactly what a production poll runs. It used
                        # to force 8/8 for smartrecruiters, which
                        # silently overrode the config
                        # and made every stored measurement describe a width
                        # production never used. Only revision reuse -- which
                        # needs the stored payload the inspector alone has --
                        # is passed.
                        harvest_options = json_api.JsonApiHarvestOptions(
                            page_step=100,
                            revision_fields=("jobAdId", "releasedDate"),
                            prior_items=prior_items,
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
                        stored = await self.result_store.load_ref(feed.source, feed.key)
                    if stored is not None:
                        items = list(stored.items)
                        envelope = stored.envelope
                        raw_size_bytes = stored.raw_size_bytes
                        reused_item_count = len(items)
                    # A 304 usually omits the validator that produced it.
                    response_etag = response_etag or validator
                    stages.record("hydrate")
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
                    detail_skipped_count=record.detail_skipped_count,
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
                error_class=error_class,
                error_detail=error_detail,
                items=tuple(items),
                envelope=envelope,
                size_bytes=size_bytes,
                network=network,
                trigger=trigger,
                response_etag=response_etag,
            )
            await self.result_store.save(snapshot)
            return snapshot

    async def feed_rows(self) -> list[dict[str, Any]]:
        feeds = [
            feed for feed in await self.db.all_feeds()
            if feed.source not in HIDDEN_SOURCES
        ]
        descriptions = await self._company_descriptions(
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
                "category": feed.type,
                "profile": source_profile(feed.source),
                "key": feed.key,
                "company_id": feed.company_id,
                "company_name": feed.company_name,
                "company_description": descriptions.get(feed.company_id),
                # Boards declare no company, so this is what labels them.
                "description": feed.description,
                "configured": True,
                "enabled": feed.enabled,
                "complete": False,
                "closes": False,
                "credentialed": False,
                "cadence_s": None,
                "next_poll_at": None,
                "source_registered": feed.source in sources.SOURCES,
                "parser_registered": feed.source in sources.PARSERS,
                "last_result": (
                    {k: v for k, v in summaries.get(feed.id, {}).items()
                     if k in (
                         "result_id", "status", "item_count", "elapsed_ms",
                         "finished_at", "error_class", "error_detail",
                     )}
                    if summaries.get(feed.id)
                    else None
                ),
            }
            for feed in feeds
        ]
        return sorted(
            rows,
            key=lambda row: (row["source"], not row["configured"], row["key"], row["feed_ref"] or ""),
        )
