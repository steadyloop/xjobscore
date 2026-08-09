"""Explicit-only, bounded manual fetch coordination.

Despite the historical module name, this file contains no scheduler, timer,
due queue, or background task.  Network work begins only in ``trigger`` or
``trigger_many`` and only for feeds supplied by that call.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from xjobscore.records import Canonical, Feed, Item, Record

from .db import Database, utc_now
from .gateway import AdapterGateway, GatewayResult
from .source_policy import classify_result

__all__ = [
    "FeedNotEnabled",
    "FeedNotFound",
    "IngestCallback",
    "ManualFetchCoordinator",
    "ManualFetchResult",
]


class FeedNotFound(LookupError):
    """An explicitly selected feed is not present in the catalog."""


class FeedNotEnabled(PermissionError):
    """A catalog feed has not been enabled in OneShot source selection."""


FeedSelection: TypeAlias = Feed | int | str
IngestCallback: TypeAlias = Callable[[GatewayResult], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ManualFetchResult:
    """User-visible result of one explicit attempt."""

    feed: Feed
    poll_id: int
    outcome: Literal["ok", "not_modified", "partial", "error"]
    health: str
    parser_state: str
    record: Record | None = None
    canonical: tuple[Canonical, ...] = ()
    error: str | None = None


class ManualFetchCoordinator:
    """Run selected fetches with a hard concurrency and selection bound."""

    def __init__(
        self,
        database: Database,
        gateway: AdapterGateway,
        *,
        max_concurrency: int = 4,
        max_selection: int = 25,
        on_ingest: IngestCallback | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if max_selection < 1:
            raise ValueError("max_selection must be positive")
        self.database = database
        self.gateway = gateway
        self.max_concurrency = max_concurrency
        self.max_selection = max_selection
        self.on_ingest = on_ingest
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Prepare storage and metadata; perform zero harvests."""
        async with self._initialize_lock:
            if self._initialized:
                return
            await self.database.initialize()
            await self.gateway.connect()
            try:
                await self.database.upsert_feeds(await self.gateway.all_feeds())
            except BaseException:
                await self.gateway.disconnect()
                raise
            self._initialized = True

    async def close(self) -> None:
        """Close owned resources.  Closing never starts or drains work."""
        if self._initialized:
            await self.gateway.disconnect()
            self._initialized = False
        await self.database.close()

    async def _resolve(self, selected: FeedSelection) -> Feed:
        if isinstance(selected, Feed):
            return selected
        if isinstance(selected, int):
            feed = await self.gateway.get_feed(selected)
        elif isinstance(selected, str):
            feed = await self.gateway.get_feed_ref(selected)
        else:
            raise TypeError("feed selection must be a Feed, integer ID, or string reference")
        if feed is None:
            raise FeedNotFound(selected)
        return feed

    async def _effective_feed(self, feed: Feed) -> Feed:
        await self.database.upsert_feed(feed)
        row = await self.database.fetch_one("SELECT state_json FROM feeds WHERE id = ?", (feed.id,))
        if row is None:
            return feed
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            state = {}
        return replace(feed, state=state if isinstance(state, dict) else {})

    async def _require_enabled(self, feed: Feed) -> None:
        selected = await self.database.fetch_one(
            """
            SELECT 1 AS selected
            FROM feeds f JOIN source_selections s ON s.feed_id = f.id
            WHERE f.id = ? AND f.enabled = 1 AND s.enabled = 1
            """,
            (feed.id,),
        )
        if selected is None:
            raise FeedNotEnabled(f"{feed.source}/{feed.key}")

    async def _begin_poll(self, feed_id: int) -> tuple[int, str]:
        started_at = utc_now()
        result = await self.database.execute(
            "INSERT INTO polls(feed_id, started_at) VALUES (?, ?)",
            (feed_id, started_at),
        )
        assert result.lastrowid is not None
        return result.lastrowid, started_at

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        text = str(exc).strip()
        return f"{type(exc).__name__}: {text}" if text else type(exc).__name__

    async def _call_ingest(self, result: GatewayResult) -> None:
        if self.on_ingest is None:
            return
        returned = self.on_ingest(result)
        if inspect.isawaitable(returned):
            await returned

    async def _finish(
        self,
        *,
        feed: Feed,
        poll_id: int,
        started_at: str,
        gateway_result: GatewayResult | None,
        error: str | None,
    ) -> ManualFetchResult:
        record = gateway_result.record if gateway_result is not None else None
        parser_state = gateway_result.parser_state if gateway_result is not None else "unknown"
        validators = record.validators if record is not None else {}
        policy = classify_result(
            feed.state,
            record=record if error is None else None,
            parser_state=parser_state,
            validators=validators,
        )
        finished_at = utc_now()
        canonical = gateway_result.canonical if gateway_result is not None else ()

        def persist(connection: sqlite3.Connection) -> None:
            if record is not None:
                for item in record.items:
                    payload_json: str | None
                    payload_text: str | None
                    if isinstance(item.payload, str):
                        payload_json = None
                        payload_text = item.payload
                    else:
                        payload_json = json.dumps(
                            item.payload, sort_keys=True, separators=(",", ":"), default=str
                        )
                        payload_text = None
                    connection.execute(
                        """
                        INSERT INTO observations (
                            poll_id, feed_id, external_id, kind, payload_json,
                            payload_text, envelope_json, observed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(poll_id, external_id) DO UPDATE SET
                            kind = excluded.kind,
                            payload_json = excluded.payload_json,
                            payload_text = excluded.payload_text,
                            envelope_json = excluded.envelope_json,
                            observed_at = excluded.observed_at
                        """,
                        (
                            poll_id,
                            feed.id,
                            item.external_id,
                            item.kind,
                            payload_json,
                            payload_text,
                            json.dumps(
                                record.envelope,
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            ),
                            started_at,
                        ),
                    )
            connection.execute(
                """
                UPDATE polls SET
                    finished_at = ?, outcome = ?, http_status = ?, item_count = ?,
                    parser_state = ?, error = ?, validators_json = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    policy.outcome,
                    record.http_status if record is not None else None,
                    len(record.items) if record is not None else 0,
                    parser_state,
                    error or (gateway_result.parser_error if gateway_result else None),
                    json.dumps(validators, sort_keys=True, separators=(",", ":")),
                    poll_id,
                ),
            )
            connection.execute(
                """
                UPDATE feeds SET
                    state_json = ?, parser_state = ?, health = ?, last_poll_id = ?,
                    last_outcome = ?, last_error = ?, last_http_status = ?,
                    last_fetched_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(policy.state, sort_keys=True, separators=(",", ":")),
                    parser_state,
                    policy.health,
                    poll_id,
                    policy.outcome,
                    error or (gateway_result.parser_error if gateway_result else None),
                    record.http_status if record is not None else None,
                    finished_at,
                    finished_at,
                    feed.id,
                ),
            )

        await self.database.transaction(persist)
        return ManualFetchResult(
            feed=feed,
            poll_id=poll_id,
            outcome=policy.outcome,
            health=policy.health,
            parser_state=parser_state,
            record=record,
            canonical=canonical,
            error=error or (gateway_result.parser_error if gateway_result else None),
        )

    async def trigger(self, selected: FeedSelection) -> ManualFetchResult:
        """Fetch exactly one explicitly selected feed."""
        if not self._initialized:
            await self.initialize()
        feed = await self._effective_feed(await self._resolve(selected))
        await self._require_enabled(feed)
        return await self._trigger_effective(feed)

    async def _trigger_effective(self, feed: Feed) -> ManualFetchResult:
        """Execute a feed that has already passed resolution and selection checks."""
        poll_id, started_at = await self._begin_poll(feed.id)
        gateway_result: GatewayResult | None = None
        error: str | None = None
        try:
            gateway_result = await self.gateway.fetch(feed)
            await self._call_ingest(gateway_result)
        except Exception as exc:
            error = self._error_text(exc)
        return await self._finish(
            feed=feed,
            poll_id=poll_id,
            started_at=started_at,
            gateway_result=gateway_result,
            error=error,
        )

    async def trigger_many(self, selected: Sequence[FeedSelection]) -> list[ManualFetchResult]:
        """Fetch only the supplied selections, preserving their result order."""
        selections = list(selected)
        if len(selections) > self.max_selection:
            raise ValueError(
                f"at most {self.max_selection} feeds may be selected, got {len(selections)}"
            )
        if not self._initialized:
            await self.initialize()
        feeds: list[Feed] = []
        for selection in selections:
            feed = await self._effective_feed(await self._resolve(selection))
            await self._require_enabled(feed)
            feeds.append(feed)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def bounded(feed: Feed) -> ManualFetchResult:
            async with semaphore:
                return await self._trigger_effective(feed)

        return list(await asyncio.gather(*(bounded(feed) for feed in feeds)))

    async def __aenter__(self) -> ManualFetchCoordinator:
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
