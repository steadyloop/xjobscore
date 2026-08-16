"""Feed the validated source registry into PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from xjobscore.sources.registry.loader import (
    Company,
    Entry,
    companies as registry_companies,
    entries as registry_entries,
)

if TYPE_CHECKING:
    from asyncpg import Connection

__all__ = ["CompanyRow", "FeedRow", "Plan", "TokenRow", "feed", "plan"]


@dataclass(frozen=True, slots=True)
class CompanyRow:
    id: int
    name: str
    domain: str | None
    domain_verified_at: datetime | None
    description: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TokenRow:
    source: str
    key: str
    company_id: int
    status: str


@dataclass(frozen=True, slots=True)
class FeedRow:
    source: str
    key: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class Plan:
    companies: tuple[CompanyRow, ...]
    tokens: tuple[TokenRow, ...]
    feeds: tuple[FeedRow, ...]

    @property
    def alias_count(self) -> int:
        return sum(len(company.aliases) for company in self.companies)


def _verified_at(company: Company) -> datetime | None:
    if not company.verified or company.domain_verified_at is None:
        return None
    return datetime.fromisoformat(company.domain_verified_at.replace("Z", "+00:00"))


def _token_status(entry: Entry) -> str:
    """Registry-derived token state.

    ``inactive`` is no longer reachable from the registry: it is reserved for
    ``reconcile_registry_inventory``, which marks tokens whose feed left the
    catalog. Polling state itself lives in PostgreSQL's ``feed.enabled``.
    """
    return "live" if entry.verified else "unverified"


def plan(
    company_rows: tuple[Company, ...] | None = None,
    feed_rows: tuple[Entry, ...] | None = None,
) -> Plan:
    """Build the exact database projection without opening a connection."""
    loaded_companies = registry_companies() if company_rows is None else company_rows
    loaded_feeds = registry_entries() if feed_rows is None else feed_rows

    companies = []
    for company in loaded_companies:
        companies.append(
            CompanyRow(
                id=company.id,
                name=company.name,
                domain=None if company.domain == "unknown" else company.domain,
                domain_verified_at=_verified_at(company),
                description=company.desc,
                aliases=company.aliases,
            )
        )

    tokens = tuple(
        TokenRow(
            source=entry.source,
            key=entry.key,
            company_id=entry.company_id,
            status=_token_status(entry),
        )
        for entry in sorted(loaded_feeds, key=lambda entry: (entry.source, entry.key))
    )
    feeds = tuple(
        FeedRow(
            source=entry.source,
            key=entry.key,
            enabled=entry.verified,
        )
        for entry in sorted(loaded_feeds, key=lambda entry: (entry.source, entry.key))
    )
    return Plan(
        companies=tuple(sorted(companies, key=lambda row: row.id)),
        tokens=tokens,
        feeds=feeds,
    )


async def feed(conn: Connection, *, dry_run: bool = False) -> Plan:
    """Make PostgreSQL's company and feed inventory match the registry."""
    import_plan = plan()
    if dry_run:
        return import_plan

    from xjobscore import store

    async with conn.transaction():
        await store.clear_registry_company_domains(
            conn, [company.id for company in import_plan.companies]
        )
        for company in import_plan.companies:
            await store.upsert_registry_company(
                conn,
                company_id=company.id,
                name=company.name,
                domain=company.domain,
                domain_verified_at=company.domain_verified_at,
                description=company.description,
            )
            for alias_norm in company.aliases:
                await store.upsert_registry_alias(
                    conn, alias_norm=alias_norm, company_id=company.id
                )
        await store.prune_registry_aliases(
            conn,
            [alias for company in import_plan.companies for alias in company.aliases],
        )
        await store.align_company_identity(conn)
        for token in import_plan.tokens:
            await store.upsert_registry_token(
                conn,
                source=token.source,
                key=token.key,
                company_id=token.company_id,
                status=token.status,
            )
        for row in import_plan.feeds:
            await store.upsert_registry_feed(
                conn,
                source=row.source,
                key=row.key,
                enabled=row.enabled,
            )
        await store.reconcile_registry_inventory(
            conn,
            [{"source": row.source, "key": row.key} for row in import_plan.feeds],
        )
    return import_plan
