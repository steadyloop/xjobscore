"""Product queries and explicit user commands for the unified board."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import re
import sqlite3
from typing import Any, Iterable
from urllib.parse import urlsplit

from xjobscore.sources.catalog import feed_ref

from .db import Database
from .dedupe import DedupeOutcome, build_identifiers, compare_postings, normalize_title, normalize_url
from .scoring import SCORING_VERSION, ScoreBand, band_for_score, route_for_band
from .taxonomy import PRIMARY_OPPORTUNITY_TYPES
from .util import dumps, loads, new_id, normalize_name, public_id, utc_now


TYPE_LABELS = {item.slug: item.value for item in PRIMARY_OPPORTUNITY_TYPES}
VALID_TYPES = frozenset(TYPE_LABELS)
VALID_TRIAGE = frozenset({"unreviewed", "saved", "queued", "dismissed"})
VALID_STAGES = frozenset(
    {
        "considering",
        "preparing",
        "submitted",
        "assessment",
        "screen",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
        "no_reply",
    }
)
VALID_SOURCE_SELECTIONS = frozenset({"all", "enabled", "disabled"})


def _job(row: dict[str, Any]) -> dict[str, Any]:
    opportunity_slug = row.get("opportunity_type") or "unclassified"
    stack = loads(row.get("stack_json"), [])
    components = loads(row.get("score_components_json"), [])
    reasons = loads(row.get("eligibility_reasons_json"), [])
    evidence = loads(row.get("opportunity_evidence_json"), [])
    source_count = int(row.get("source_count") or 0)
    authoritative = bool(row.get("authoritative_open"))
    return {
        "id": row["id"],
        "company": row.get("company_name") or "Unknown company",
        "company_name": row.get("company_name") or "Unknown company",
        "title": row.get("title") or "Untitled opportunity",
        "opportunity_type": TYPE_LABELS.get(opportunity_slug, ""),
        "opportunity_type_slug": opportunity_slug,
        "needs_classification": opportunity_slug in {"unclassified", "review"},
        "opportunity_confidence": row.get("opportunity_confidence", 0),
        "opportunity_evidence": evidence,
        "location": row.get("location"),
        "workplace_type": row.get("workplace_type"),
        "employment_type": row.get("employment_type"),
        "description_html": row.get("description_html"),
        "posting_url": row.get("posting_url"),
        "apply_url": row.get("apply_url") or row.get("posting_url"),
        "published_at": row.get("published_at"),
        "deadline_at": row.get("deadline_at"),
        "first_seen_at": row.get("first_seen_at"),
        "last_seen_at": row.get("last_seen_at"),
        "status": row.get("status"),
        "eligibility": row.get("eligibility"),
        "eligibility_reasons": reasons,
        "semantic_stack": stack,
        "stack": stack,
        "score": int(row.get("score") or 0),
        "score_band": row.get("score_band"),
        "submission_route": {
            "band": row.get("score_band"),
            "submit_within_hours": row.get("submit_within_hours"),
            "auto_submit": False,
            "instruction": row.get("route_instruction"),
        },
        "score_components": components,
        "resume_variant": row.get("resume_variant"),
        "source_count": source_count,
        "source_evidence": (
            f"{source_count} unified source{'s' if source_count != 1 else ''} · "
            + ("official source active" if authoritative else "origin verification needed")
        ),
        "triage_state": row.get("triage_state"),
        "changed_recently": bool(row.get("changed_recently")),
        "last_changed_at": row.get("last_changed_at"),
        "version": row.get("version"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class BoardRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _restore_job_snapshot(
        connection: sqlite3.Connection, snapshot: dict[str, Any], now: str
    ) -> None:
        if not snapshot or not snapshot.get("id"):
            return
        allowed = {
            row[1]
            for row in connection.execute("PRAGMA table_info(job_entities)").fetchall()
        } - {"id"}
        columns = [name for name in snapshot if name in allowed]
        if not columns:
            return
        assignments = ", ".join(f"{name} = ?" for name in columns)
        connection.execute(
            f"UPDATE job_entities SET {assignments} WHERE id = ?",
            (*[snapshot[name] for name in columns], snapshot["id"]),
        )
        connection.execute(
            "UPDATE job_entities SET version = version + 1, updated_at = ? WHERE id = ?",
            (now, snapshot["id"]),
        )

    async def health(self) -> dict[str, Any]:
        counts = await self.database.fetch_one(
            """
            SELECT
                (SELECT COUNT(*) FROM feeds) AS feeds,
                (SELECT COUNT(*) FROM feeds f JOIN source_selections s ON s.feed_id = f.id
                  WHERE f.enabled = 1 AND s.enabled = 1) AS enabled_sources,
                (SELECT COUNT(*) FROM polls) AS manual_fetches,
                (SELECT COUNT(*) FROM job_entities WHERE status != 'merged') AS jobs,
                (SELECT COUNT(*) FROM dedupe_candidates WHERE state IN ('pending', 'deferred')) AS dedupe_review,
                (SELECT COUNT(*) FROM job_applications) AS applications
            """
        )
        return {
            "ok": True,
            "mode": "manual_fetch_only",
            "polling": False,
            "counts": counts or {},
        }

    async def list_jobs(
        self,
        *,
        opportunity_types: Iterable[str] = (),
        q: str = "",
        state: str | None = None,
        eligibility: str | None = None,
        sort: str = "fresh",
        include_unclassified: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses = ["status IN ('open', 'conflicting')"]
        params: list[Any] = []
        selected_types = tuple(dict.fromkeys(t for t in opportunity_types if t in VALID_TYPES))
        type_values = (*selected_types, *(() if not include_unclassified else ("unclassified", "review")))
        if type_values:
            clauses.append(f"opportunity_type IN ({','.join('?' for _ in type_values)})")
            params.extend(type_values)
        if state in VALID_TRIAGE:
            clauses.append("triage_state = ?")
            params.append(state)
        elif state:
            clauses.append("1 = 0")
        if eligibility in {"eligible", "review", "ineligible"}:
            clauses.append("eligibility = ?")
            params.append(eligibility)
        if q.strip():
            clauses.append(
                "(company_name LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR description_html LIKE ? ESCAPE '\\')"
            )
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{escaped}%"
            params.extend((term, term, term))
        ordering = {
            "score": "score DESC, first_seen_at DESC, id",
            "deadline": "CASE WHEN deadline_at IS NULL THEN 1 ELSE 0 END, deadline_at, score DESC, id",
            "fresh": "COALESCE(published_at, first_seen_at) DESC, score DESC, id",
            "action": "CASE score_band WHEN 'one_shot' THEN 1 WHEN 'tailored_fast' THEN 2 WHEN 'volume' THEN 3 ELSE 4 END, score DESC, first_seen_at DESC, id",
        }.get(sort, "COALESCE(published_at, first_seen_at) DESC, score DESC, id")
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        where = " AND ".join(clauses)
        total_row = await self.database.fetch_one(
            f"SELECT COUNT(*) AS count FROM job_entities WHERE {where}", tuple(params)
        )
        rows = await self.database.fetch_all(
            f"""
            SELECT j.*,
                   EXISTS (
                     SELECT 1 FROM posting_revisions changed
                     JOIN posting_observations changed_observation
                       ON changed_observation.id = changed.observation_id
                     WHERE changed_observation.job_entity_id = j.id
                       AND changed.revision_no > 1
                       AND datetime(changed.created_at) >= datetime('now', '-2 days')
                   ) AS changed_recently,
                   (
                     SELECT MAX(changed.created_at) FROM posting_revisions changed
                     JOIN posting_observations changed_observation
                       ON changed_observation.id = changed.observation_id
                     WHERE changed_observation.job_entity_id = j.id
                       AND changed.revision_no > 1
                   ) AS last_changed_at
            FROM job_entities j
            WHERE {where}
            ORDER BY {ordering}
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
        return {
            "jobs": [_job(row) for row in rows],
            "total": int(total_row["count"] if total_row else 0),
            "limit": limit,
            "offset": offset,
        }

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = await self.database.fetch_one(
            "SELECT * FROM job_entities WHERE id = ? AND status != 'merged'", (job_id,)
        )
        if row is None:
            return None
        result = _job(row)
        observations = await self.database.fetch_all(
            """
            SELECT id, source, feed_key, external_id, title, location,
                   posting_url, apply_url, requisition_id, authority, status,
                   first_seen_at, last_seen_at, revision_no
            FROM posting_observations
            WHERE job_entity_id = ?
            ORDER BY CASE authority WHEN 'origin' THEN 3 WHEN 'manual' THEN 2 ELSE 1 END DESC,
                     last_seen_at DESC
            """,
            (job_id,),
        )
        requirements = await self.database.fetch_all(
            """
            SELECT technology, level, evidence, algorithm_version
            FROM job_requirements WHERE job_entity_id = ?
            ORDER BY CASE level WHEN 'required' THEN 1 WHEN 'preferred' THEN 2 ELSE 3 END,
                     technology
            """,
            (job_id,),
        )
        applications = await self.database.fetch_all(
            "SELECT id, stage, submitted_at, next_action, next_due_at, resume_variant, updated_at FROM job_applications WHERE job_entity_id = ? ORDER BY updated_at DESC",
            (job_id,),
        )
        related_openings = await self.database.fetch_all(
            """
            SELECT d.id AS relationship_id, d.confidence, d.reasons_json,
                   other.id, other.company_name, other.title, other.location,
                   other.opportunity_type, other.score
            FROM dedupe_candidates d
            JOIN job_entities other ON other.id = CASE
                WHEN d.entity_a_id = ? THEN d.entity_b_id ELSE d.entity_a_id END
            WHERE d.outcome = 'related' AND d.state = 'related'
              AND (d.entity_a_id = ? OR d.entity_b_id = ?)
              AND other.status != 'merged'
            ORDER BY d.confidence DESC, other.score DESC
            """,
            (job_id, job_id, job_id),
        )
        result.update(
            observations=observations,
            requirements=requirements,
            applications=applications,
            related_openings=[
                {
                    "relationship_id": item["relationship_id"],
                    "id": item["id"],
                    "company": item["company_name"],
                    "title": item["title"],
                    "location": item["location"],
                    "opportunity_type": TYPE_LABELS.get(
                        item["opportunity_type"], ""
                    ),
                    "score": item["score"],
                    "reason": "; ".join(loads(item["reasons_json"], [])),
                }
                for item in related_openings
            ],
        )
        return result

    async def today(
        self, opportunity_types: Iterable[str] = (), include_unclassified: bool = False
    ) -> dict[str, Any]:
        clauses = ["j.status IN ('open', 'conflicting')", "j.triage_state != 'dismissed'"]
        params: list[Any] = []
        selected_types = tuple(dict.fromkeys(t for t in opportunity_types if t in VALID_TYPES))
        type_values = (*selected_types, *(() if not include_unclassified else ("unclassified", "review")))
        if type_values:
            clauses.append(f"j.opportunity_type IN ({','.join('?' for _ in type_values)})")
            params.extend(type_values)
        clauses.append(
            "(EXISTS (SELECT 1 FROM job_applications due "
            "         WHERE due.job_entity_id = j.id AND due.next_due_at IS NOT NULL "
            "           AND datetime(due.next_due_at) <= datetime('now', '+7 days')) "
            " OR EXISTS (SELECT 1 FROM posting_revisions changed "
            "            JOIN posting_observations changed_observation "
            "              ON changed_observation.id = changed.observation_id "
            "            WHERE changed_observation.job_entity_id = j.id "
            "              AND changed.revision_no > 1 "
            "              AND datetime(changed.created_at) >= datetime('now', '-2 days')) "
            " OR (NOT EXISTS (SELECT 1 FROM job_applications existing "
            "                 WHERE existing.job_entity_id = j.id) "
            "     AND (j.triage_state IN ('queued', 'saved') "
            "          OR (j.eligibility = 'eligible' AND j.score >= 70) "
            "          OR (j.deadline_at IS NOT NULL "
            "              AND j.deadline_at <= datetime('now', '+7 days')))))"
        )
        rows = await self.database.fetch_all(
            f"""
            SELECT j.*,
                   CASE
                     WHEN EXISTS (
                       SELECT 1 FROM job_applications a
                       WHERE a.job_entity_id = j.id AND a.next_due_at IS NOT NULL
                         AND datetime(a.next_due_at) <= datetime('now', '+7 days')
                     ) THEN 'Application action due'
                     WHEN EXISTS (
                       SELECT 1 FROM posting_revisions changed
                       JOIN posting_observations changed_observation
                         ON changed_observation.id = changed.observation_id
                       WHERE changed_observation.job_entity_id = j.id
                         AND changed.revision_no > 1
                         AND datetime(changed.created_at) >= datetime('now', '-2 days')
                     ) THEN 'Posting changed'
                     WHEN j.deadline_at IS NOT NULL AND j.deadline_at <= datetime('now', '+7 days')
                       THEN 'Posting deadline approaching'
                     WHEN j.triage_state = 'queued' THEN 'Queued to apply'
                     WHEN j.score >= 90 THEN 'One-shot review'
                     WHEN j.score >= 70 THEN 'Tailored application window'
                     ELSE 'Saved for review'
                   END AS today_reason
            FROM job_entities j
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE WHEN j.deadline_at IS NOT NULL AND j.deadline_at <= datetime('now', '+7 days') THEN 0 ELSE 1 END,
                CASE j.score_band WHEN 'one_shot' THEN 1 WHEN 'tailored_fast' THEN 2 WHEN 'volume' THEN 3 ELSE 4 END,
                j.score DESC, j.first_seen_at DESC
            LIMIT 200
            """,
            tuple(params),
        )
        items = []
        for row in rows:
            item = _job(row)
            item["today_reason"] = row.get("today_reason")
            items.append(item)
        return {"items": items, "total": len(items)}

    async def set_triage(self, job_id: str, state: str) -> dict[str, Any] | None:
        if state not in VALID_TRIAGE:
            raise ValueError("unknown triage state")
        now = utc_now()
        def operation(connection: sqlite3.Connection) -> bool:
            if state == "queued":
                job = connection.execute(
                    "SELECT status, eligibility FROM job_entities "
                    "WHERE id = ? AND status != 'merged'",
                    (job_id,),
                ).fetchone()
                if job is None:
                    return False
                if job["status"] != "open" or job["eligibility"] != "eligible":
                    raise ValueError("queueing requires a currently open, eligible job")
            result = connection.execute(
                "UPDATE job_entities SET triage_state = ?, version = version + 1, updated_at = ? WHERE id = ? AND status != 'merged'",
                (state, now, job_id),
            )
            if not result.rowcount:
                return False
            connection.execute(
                "INSERT INTO events(aggregate_type, aggregate_id, event_type, data_json, occurred_at) VALUES ('job', ?, 'triage_changed', ?, ?)",
                (job_id, dumps({"state": state}), now),
            )
            return True

        changed = await self.database.transaction(operation)
        if not changed:
            return None
        return await self.get_job(job_id)

    async def set_classification(
        self, job_id: str, opportunity_type: str, rationale: str
    ) -> dict[str, Any] | None:
        if opportunity_type not in VALID_TYPES:
            raise ValueError("unknown opportunity type")
        now = utc_now()

        def operation(connection: sqlite3.Connection) -> bool:
            entity = connection.execute(
                "SELECT * FROM job_entities WHERE id = ? AND status != 'merged'",
                (job_id,),
            ).fetchone()
            if entity is None:
                return False

            reasons = [
                reason
                for reason in loads(entity["eligibility_reasons_json"], [])
                if reason != "Review: window_compatible"
            ]
            components = list(loads(entity["score_components_json"], []))
            window_component_found = False
            for component in components:
                if component.get("name") != "window_fit":
                    continue
                component["points"] = 15
                component["explanation"] = "human-confirmed exact work term"
                window_component_found = True

            eligibility = str(entity["eligibility"])
            if eligibility != "ineligible":
                remaining_reviews = [
                    reason for reason in reasons if str(reason).startswith("Review:")
                ]
                eligibility = (
                    "eligible"
                    if window_component_found and not remaining_reviews
                    else "review"
                )

            score = int(entity["score"])
            if window_component_found:
                score = max(
                    0,
                    min(
                        100,
                        sum(int(component.get("points", 0)) for component in components),
                    ),
                )
            score_band = (
                band_for_score(score) if eligibility == "eligible" else ScoreBand.LOG_ONLY
            )
            route = route_for_band(score_band)
            submit_within_hours = (
                int(route.submit_within.total_seconds() // 3600)
                if route.submit_within is not None
                else None
            )

            result = connection.execute(
                """
                UPDATE job_entities SET opportunity_type = ?,
                    opportunity_confidence = 1.0,
                    opportunity_evidence_json = ?, manual_opportunity_type = ?,
                    manual_classification_evidence = ?, version = version + 1,
                    eligibility = ?, eligibility_reasons_json = ?,
                    score = ?, score_band = ?, score_components_json = ?,
                    route_instruction = ?, submit_within_hours = ?, auto_submit = 0,
                    updated_at = ?
                WHERE id = ? AND status != 'merged'
                """,
                (
                    opportunity_type,
                    dumps([f"Human correction: {rationale}"]),
                    opportunity_type,
                    rationale,
                    eligibility,
                    dumps(reasons),
                    score,
                    score_band.value,
                    dumps(components),
                    route.instruction,
                    submit_within_hours,
                    now,
                    job_id,
                ),
            )
            if not result.rowcount:
                return False
            connection.execute(
                """
                INSERT INTO classification_snapshots(
                    id, job_entity_id, opportunity_type, eligibility,
                    evidence_json, algorithm_version, created_at, versions_json
                )
                SELECT ?, id, ?, eligibility, ?, 'human-correction/1', ?, ?
                FROM job_entities WHERE id = ?
                """,
                (
                    new_id(), opportunity_type,
                    dumps({
                        "rationale": rationale,
                        "actor": "human",
                        "eligibility_reasons": reasons,
                    }),
                    now, dumps({"opportunity": "human-correction/1"}), job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO score_snapshots(
                    id, job_entity_id, score, band, components_json,
                    resume_variant, algorithm_version, created_at, route_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    job_id,
                    score,
                    score_band.value,
                    dumps(components),
                    entity["resume_variant"],
                    SCORING_VERSION,
                    now,
                    dumps(
                        {
                            "instruction": route.instruction,
                            "submit_within_hours": submit_within_hours,
                            "auto_submit": False,
                        }
                    ),
                ),
            )
            return True

        changed = await self.database.transaction(operation)
        return await self.get_job(job_id) if changed else None

    @staticmethod
    def _source_item(row: dict[str, Any]) -> dict[str, Any]:
        selected = bool(row.get("selected"))
        return {
            "feed_ref": feed_ref(row["source"], row["feed_key"]),
            "source": row["source"],
            "key": row["feed_key"],
            "company_id": row.get("company_id"),
            "company_name": row.get("company_name"),
            "name": row.get("company_name") or f"{row['source']} · {row['feed_key']}",
            "selected": selected,
            "status": (
                "quarantined"
                if row.get("snapshot_state") == "quarantined"
                else row.get("health") or "never_fetched"
            ),
            "snapshot_state": row.get("snapshot_state"),
            "snapshot_reason": row.get("snapshot_reason"),
            "parser_state": row.get("parser_state"),
            "last_manual_fetch": row.get("last_fetched_at"),
            "last_error": row.get("last_error"),
            "last_http_status": row.get("last_http_status"),
            "description": f"{row['source']} / {row['feed_key']}",
        }

    @staticmethod
    def _selection_clause(selection: str) -> str | None:
        if selection == "enabled":
            return "COALESCE(s.enabled, 0) = 1"
        if selection == "disabled":
            return "COALESCE(s.enabled, 0) = 0"
        if selection == "all":
            return None
        raise ValueError("selection must be all, enabled, or disabled")

    async def list_sources(
        self,
        *,
        q: str = "",
        source: str | None = None,
        selection: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if selection not in VALID_SOURCE_SELECTIONS:
            raise ValueError("selection must be all, enabled, or disabled")
        clauses = ["f.enabled = 1"]
        params: list[Any] = []
        selection_clause = self._selection_clause(selection)
        if selection_clause:
            clauses.append(selection_clause)
        if source:
            clauses.append("f.source = ?")
            params.append(source)
        if q.strip():
            clauses.append("(f.source LIKE ? OR f.feed_key LIKE ? OR f.company_name LIKE ?)")
            term = f"%{q.strip()}%"
            params.extend((term, term, term))
        where = " AND ".join(clauses)
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        rows = await self.database.fetch_all(
            f"""
            SELECT f.*, COALESCE(s.enabled, 0) AS selected
            FROM feeds f LEFT JOIN source_selections s ON s.feed_id = f.id
            WHERE {where}
            ORDER BY f.source, COALESCE(f.company_name, f.feed_key), f.feed_key
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
        total = await self.database.fetch_one(
            f"""
            SELECT COUNT(*) AS count
            FROM feeds f LEFT JOIN source_selections s ON s.feed_id = f.id
            WHERE {where}
            """,
            tuple(params),
        )
        return {
            "sources": [self._source_item(row) for row in rows],
            "total": int(total["count"] if total else 0),
            "limit": limit,
            "offset": offset,
            "selection": selection,
        }

    async def list_source_groups(
        self, *, q: str = "", selection: str = "enabled"
    ) -> dict[str, Any]:
        if selection not in VALID_SOURCE_SELECTIONS:
            raise ValueError("selection must be all, enabled, or disabled")
        rows = await self.database.fetch_all(
            """
            SELECT f.*, COALESCE(s.enabled, 0) AS selected
            FROM feeds f LEFT JOIN source_selections s ON s.feed_id = f.id
            WHERE f.enabled = 1
            ORDER BY f.source, COALESCE(f.company_name, f.feed_key), f.feed_key
            """
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["source"], []).append(row)
        query = q.strip().casefold()
        vendors: list[dict[str, Any]] = []
        for source, source_rows in grouped.items():
            source_matches = bool(query and query in source.casefold())
            visible_rows = []
            for row in source_rows:
                selected = bool(row.get("selected"))
                if selection == "enabled" and not selected:
                    continue
                if selection == "disabled" and selected:
                    continue
                if query and not source_matches and not any(
                    query in str(value or "").casefold()
                    for value in (row.get("company_name"), row.get("feed_key"))
                ):
                    continue
                visible_rows.append(row)
            if not visible_rows:
                continue
            enabled_count = sum(bool(row.get("selected")) for row in source_rows)
            attention_count = sum(
                (row.get("snapshot_state") == "quarantined")
                or row.get("health") in {"error", "partial", "missing_parser"}
                for row in source_rows
            )
            vendors.append(
                {
                    "source": source,
                    "available_count": len(source_rows),
                    "enabled_count": enabled_count,
                    "attention_count": attention_count,
                    "fetched_count": sum(bool(row.get("last_fetched_at")) for row in source_rows),
                    "feeds": [self._source_item(row) for row in visible_rows],
                }
            )
        return {
            "vendors": vendors,
            "vendor_count": len(vendors),
            "total_available": len(rows),
            "total_enabled": sum(bool(row.get("selected")) for row in rows),
            "selection": selection,
            "query": q,
        }

    async def set_source_selection(
        self, feed_reference: str, enabled: bool
    ) -> dict[str, Any] | None:
        rows = await self.database.fetch_all(
            "SELECT * FROM feeds WHERE enabled = 1 ORDER BY source, feed_key"
        )
        target = next(
            (
                row
                for row in rows
                if feed_ref(row["source"], row["feed_key"]) == feed_reference
            ),
            None,
        )
        if target is None:
            return None
        now = utc_now()
        await self.database.execute(
            """
            INSERT INTO source_selections(feed_id, enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(feed_id) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (target["id"], int(enabled), now),
        )
        target["selected"] = int(enabled)
        return self._source_item(target)

    async def set_vendor_selection(self, source: str, enabled: bool) -> dict[str, Any] | None:
        now = utc_now()

        def operation(connection: sqlite3.Connection) -> int:
            rows = connection.execute(
                "SELECT id FROM feeds WHERE enabled = 1 AND source = ?",
                (source,),
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO source_selections(feed_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                ((row["id"], int(enabled), now) for row in rows),
            )
            return len(rows)

        affected = await self.database.transaction(operation)
        if not affected:
            return None
        return {
            "source": source,
            "enabled": enabled,
            "affected": affected,
        }

    async def list_dedupe(self) -> dict[str, Any]:
        rows = await self.database.fetch_all(
            """
            SELECT d.*, a.company_name AS a_company, a.title AS a_title,
                   a.location AS a_location, b.company_name AS b_company,
                   b.title AS b_title, b.location AS b_location
            FROM dedupe_candidates d
            JOIN job_entities a ON a.id = d.entity_a_id
            JOIN job_entities b ON b.id = d.entity_b_id
            WHERE d.state IN ('pending', 'deferred', 'merged')
            ORDER BY CASE d.state WHEN 'pending' THEN 1 WHEN 'deferred' THEN 2 ELSE 3 END,
                     d.confidence DESC, d.created_at
            """
        )
        return {
            "items": [
                {
                    "id": row["id"],
                    "confidence": row["confidence"],
                    "reason": "; ".join(loads(row["reasons_json"], [])),
                    "outcome": row["outcome"],
                    "state": row["state"],
                    "algorithm_version": row.get("algorithm_version"),
                    "shared_identifiers": loads(row.get("shared_identifiers_json"), []),
                    "candidate": {
                        "id": row["entity_a_id"], "company": row["a_company"],
                        "title": row["a_title"], "location": row["a_location"],
                    },
                    "match": {
                        "id": row["entity_b_id"], "company": row["b_company"],
                        "title": row["b_title"], "location": row["b_location"],
                    },
                }
                for row in rows
            ]
        }

    async def decide_dedupe(
        self, candidate_id: str, action: str, rationale: str = ""
    ) -> dict[str, Any] | None:
        aliases = {"keep_both": "keep_separate"}
        action = aliases.get(action, action)
        if action not in {"merge", "keep_separate", "defer", "reverse"}:
            raise ValueError("unknown dedupe decision")
        now = utc_now()

        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            candidate = connection.execute(
                "SELECT * FROM dedupe_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                return None
            if action == "merge":
                if candidate["state"] not in {"pending", "deferred", "reversed"}:
                    raise ValueError(
                        f"candidate cannot be merged from state {candidate['state']!r}"
                    )
                entity_rows = connection.execute(
                    "SELECT * FROM job_entities WHERE id IN (?, ?)",
                    (candidate["entity_a_id"], candidate["entity_b_id"]),
                ).fetchall()
                if len(entity_rows) != 2:
                    raise ValueError("both merge entities must exist")
                if any(row["status"] == "merged" for row in entity_rows):
                    raise ValueError("both merge entities must be live and non-merged")

                # A dedupe candidate is a durable review record, while its
                # observations can change. Rebuild the current identities before
                # a human merge so a stale candidate cannot bypass new conflicts.
                from .ingest import BoardIngestor

                identities_a = BoardIngestor._identity_for_entity(
                    connection, candidate["entity_a_id"]
                )
                identities_b = BoardIngestor._identity_for_entity(
                    connection, candidate["entity_b_id"]
                )
                if not identities_a or not identities_b:
                    raise ValueError("both merge entities must have current identity evidence")
                current_comparisons = [
                    compare_postings(identity_a, identity_b)
                    for identity_a in identities_a
                    for identity_b in identities_b
                ]
                if any(
                    comparison.outcome in {DedupeOutcome.NEW, DedupeOutcome.RELATED}
                    for comparison in current_comparisons
                ):
                    raise ValueError(
                        "candidate evidence changed and no longer supports a merge"
                    )
                ranked = sorted(
                    entity_rows,
                    key=lambda row: (
                        int(row["authoritative_open"] or 0),
                        int(row["status"] in {"open", "conflicting"}),
                        len(row["description_html"] or ""),
                        row["last_seen_at"] or "",
                    ),
                    reverse=True,
                )
                survivor = ranked[0]["id"]
                absorbed = ranked[1]["id"]
                before = {
                    "survivor": dict(ranked[0]),
                    "absorbed": dict(ranked[1]),
                }
                moved = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM posting_observations WHERE job_entity_id = ?",
                        (absorbed,),
                    ).fetchall()
                ]
                moved_applications = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM job_applications WHERE job_entity_id = ?",
                        (absorbed,),
                    ).fetchall()
                ]
                connection.execute(
                    "UPDATE posting_observations SET job_entity_id = ? WHERE job_entity_id = ?",
                    (survivor, absorbed),
                )
                connection.execute(
                    "UPDATE job_entities SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id = ?",
                    (survivor, now, absorbed),
                )
                connection.execute(
                    "UPDATE job_applications SET job_entity_id = ? WHERE job_entity_id = ?",
                    (survivor, absorbed),
                )
                connection.execute(
                    """
                    INSERT INTO merge_operations(
                        id, candidate_id, survivor_id, absorbed_id,
                        moved_observation_ids_json, created_at,
                        moved_application_ids_json, before_state_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(), candidate_id, survivor, absorbed, dumps(moved), now,
                        dumps(moved_applications), dumps(before),
                    ),
                )
                connection.execute(
                    "UPDATE dedupe_candidates SET state = 'merged', updated_at = ? WHERE id = ?",
                    (now, candidate_id),
                )
                BoardIngestor._refresh_lifecycle_aggregate(connection, survivor, now)
            elif action == "reverse":
                merge = connection.execute(
                    "SELECT * FROM merge_operations WHERE candidate_id = ? AND reversed_at IS NULL ORDER BY created_at DESC LIMIT 1",
                    (candidate_id,),
                ).fetchone()
                if merge is None:
                    raise ValueError("candidate has no active merge to reverse")
                moved = loads(merge["moved_observation_ids_json"], [])
                for observation_id in moved:
                    connection.execute(
                        "UPDATE posting_observations SET job_entity_id = ? WHERE id = ?",
                        (merge["absorbed_id"], observation_id),
                    )
                    connection.execute(
                        """
                        UPDATE link_evidence SET review_state = 'rejected'
                        WHERE observation_a_id = ? OR observation_b_id = ?
                        """,
                        (observation_id, observation_id),
                    )
                moved_applications = loads(merge["moved_application_ids_json"], [])
                for application_id in moved_applications:
                    connection.execute(
                        "UPDATE job_applications SET job_entity_id = ?, updated_at = ? WHERE id = ?",
                        (merge["absorbed_id"], now, application_id),
                    )
                before = loads(merge["before_state_json"], {})
                # The survivor is editable while merged. Restoring its pre-merge
                # whole-row snapshot would erase legitimate later human changes.
                self._restore_job_snapshot(connection, before.get("absorbed", {}), now)
                from .ingest import BoardIngestor

                BoardIngestor._refresh_lifecycle_aggregate(
                    connection, merge["survivor_id"], now
                )
                BoardIngestor._refresh_lifecycle_aggregate(
                    connection, merge["absorbed_id"], now
                )
                connection.execute(
                    "UPDATE merge_operations SET reversed_at = ? WHERE id = ?",
                    (now, merge["id"]),
                )
                connection.execute(
                    "UPDATE dedupe_candidates SET state = 'reversed', updated_at = ? WHERE id = ?",
                    (now, candidate_id),
                )
            else:
                state = "kept_separate" if action == "keep_separate" else "deferred"
                connection.execute(
                    "UPDATE dedupe_candidates SET state = ?, updated_at = ? WHERE id = ?",
                    (state, now, candidate_id),
                )
            connection.execute(
                "INSERT INTO dedupe_decisions(id, candidate_id, action, rationale, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), candidate_id, action, rationale, now),
            )
            return {"id": candidate_id, "action": action, "updated_at": now}

        return await self.database.transaction(operation)

    async def resolve_capture(self, url: str) -> dict[str, Any]:
        try:
            identifiers = build_identifiers(urls=(url,))
        except ValueError:
            identifiers = ()
        matches: dict[str, dict[str, Any]] = {}
        for identifier in identifiers:
            rows = await self.database.fetch_all(
                """
                SELECT DISTINCT e.*
                FROM posting_identifiers i
                JOIN posting_observations o ON o.id = i.observation_id
                JOIN job_entities e ON e.id = o.job_entity_id
                WHERE i.namespace = ? AND i.scope = ? AND i.value = ?
                  AND e.status != 'merged'
                """,
                identifier.key,
            )
            for row in rows:
                job = _job(row)
                job["reason"] = f"Exact {identifier.namespace} identifier"
                matches[job["id"]] = job
        return {
            "summary": (
                f"{len(matches)} exact board match{'es' if len(matches) != 1 else ''} found."
                if matches
                else "No deterministic board match found; capture will remain reviewable."
            ),
            "items": list(matches.values()),
            "exact": bool(matches),
        }

    async def capture_job(
        self,
        *,
        url: str,
        company: str | None = None,
        title: str | None = None,
        description: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        resolved = await self.resolve_capture(url)
        if resolved["items"]:
            return resolved["items"][0]
        parsed = urlsplit(url if "://" in url else f"https://{url}")
        if not parsed.hostname:
            raise ValueError("capture URL must be HTTP(S)")
        normalized_url = normalize_url(url)
        inferred_company = company or parsed.hostname.split(".")[0].replace("-", " ").title()
        inferred_title = title or (parsed.path.rstrip("/").split("/")[-1].replace("-", " ").title() or "Captured opportunity")
        now = utc_now()
        company_id = public_id("company", normalize_name(inferred_company))
        entity_id = new_id()
        observation_id = public_id("manual-observation", normalized_url)
        identifiers = build_identifiers(urls=(normalized_url,))

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO companies(id, display_name, normalized_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (company_id, inferred_company, normalize_name(inferred_company), now, now),
            )
            connection.execute(
                """
                INSERT INTO job_entities(
                    id, company_id, company_name, title, title_normalized,
                    opportunity_type, opportunity_confidence, location,
                    description_html, posting_url, apply_url, first_seen_at,
                    last_seen_at, status, eligibility, eligibility_reasons_json,
                    score, score_band, resume_variant, authoritative_open,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'unclassified', 0, ?, ?, ?, ?, ?, ?,
                          'open', 'review', '["Manual capture requires classification"]',
                          0, 'log_only', 'backend-infra', 0, ?, ?)
                """,
                (
                    entity_id, company_id, inferred_company, inferred_title,
                    normalize_title(inferred_title), location or None,
                    description or None, normalized_url, normalized_url,
                    now, now, now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO posting_observations(
                    id, source, feed_key, external_id, job_entity_id, company_raw,
                    title, location, description_html, posting_url, apply_url,
                    authority, status, content_hash, first_seen_at, last_seen_at
                ) VALUES (?, 'manual', 'private', ?, ?, ?, ?, ?, ?, ?, ?,
                          'manual', 'open', ?, ?, ?)
                """,
                (
                    observation_id, observation_id, entity_id, inferred_company,
                    inferred_title, location or None, description or None,
                    normalized_url, normalized_url, public_id("manual-content", normalized_url, description),
                    now, now,
                ),
            )
            for identifier in identifiers:
                connection.execute(
                    "INSERT INTO posting_identifiers(observation_id, namespace, scope, value, provenance, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (observation_id, identifier.namespace, identifier.scope, identifier.value, "manual", now),
                )

        await self.database.transaction(operation)
        job = await self.get_job(entity_id)
        assert job is not None
        return job

    async def create_application(
        self,
        job_id: str,
        *,
        stage: str = "submitted",
        note: str = "",
        submitted_at: str | None = None,
        resume_variant: str | None = None,
        source_observation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if stage not in {"considering", "preparing", "submitted"}:
            raise ValueError("application must start as considering, preparing, or submitted")
        job = await self.get_job(job_id)
        if job is None:
            return None
        if stage in {"preparing", "submitted"}:
            if job["status"] != "open":
                raise ValueError(f"{stage} requires a currently open job")
            if job["eligibility"] != "eligible":
                raise ValueError(f"{stage} requires an eligible job")
        existing_application = await self.database.fetch_one(
            """
            SELECT id, stage FROM job_applications
            WHERE job_entity_id = ? AND stage IN (
                'considering', 'preparing', 'submitted', 'assessment',
                'screen', 'interview', 'offer'
            )
            ORDER BY updated_at DESC LIMIT 1
            """,
            (job_id,),
        )
        if existing_application is not None:
            can_confirm_plan = (
                stage == "submitted"
                and existing_application["stage"] in {"considering", "preparing"}
            )
            if not can_confirm_plan:
                return await self.get_application(existing_application["id"])
        if source_observation_id is None and job["observations"]:
            source_observation_id = job["observations"][0]["id"]
        submitted = (submitted_at or utc_now()) if stage == "submitted" else None
        if submitted is not None and len(submitted) == 10:
            submitted = datetime.combine(date.fromisoformat(submitted), time.min, tzinfo=timezone.utc).isoformat()
        application_id = (
            existing_application["id"] if existing_application is not None else new_id()
        )
        from_stage = (
            existing_application["stage"] if existing_application is not None else None
        )
        now = utc_now()
        resume = resume_variant or job["resume_variant"] or "backend-infra"
        if stage == "submitted":
            assert submitted is not None
            next_action = "Follow up if no response"
            next_due_at = (
                datetime.fromisoformat(submitted) + timedelta(days=14)
            ).isoformat()
            event_type = "submitted"
            event_at = submitted
            triage_state = "unreviewed"
        else:
            hours = job["submission_route"].get("submit_within_hours") or 24
            next_action = (
                job["submission_route"].get("instruction")
                or "Review the official posting and prepare materials."
            )
            next_due_at = (
                datetime.now(timezone.utc) + timedelta(hours=hours)
            ).isoformat()
            event_type = "stage_changed"
            event_at = now
            triage_state = "queued" if stage == "preparing" else "saved"

        def operation(connection: sqlite3.Connection) -> None:
            if stage in {"preparing", "submitted"}:
                current_job = connection.execute(
                    "SELECT status, eligibility FROM job_entities WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if current_job is None or current_job["status"] != "open":
                    raise ValueError(f"{stage} requires a currently open job")
                if current_job["eligibility"] != "eligible":
                    raise ValueError(f"{stage} requires an eligible job")
            apply_url_snapshot = job["apply_url"]
            company_snapshot = job["company"]
            title_snapshot = job["title"]
            description_snapshot = job["description_html"]
            if source_observation_id is not None:
                selected = connection.execute(
                    """
                    SELECT apply_url, posting_url, company_raw, title, description_html
                    FROM posting_observations
                    WHERE id = ? AND job_entity_id = ?
                    """,
                    (source_observation_id, job_id),
                ).fetchone()
                if selected is None:
                    raise ValueError("source observation does not belong to this job")
                apply_url_snapshot = selected["apply_url"] or selected["posting_url"]
                company_snapshot = selected["company_raw"] or company_snapshot
                title_snapshot = selected["title"] or title_snapshot
                description_snapshot = selected["description_html"]
            if existing_application is None:
                connection.execute(
                    """
                    INSERT INTO job_applications(
                        id, job_entity_id, source_observation_id, stage, resume_variant,
                        apply_url_snapshot, company_snapshot, title_snapshot,
                        description_snapshot, submitted_at, next_action, next_due_at,
                        note, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        application_id, job_id, source_observation_id, stage, resume,
                        apply_url_snapshot, company_snapshot, title_snapshot,
                        description_snapshot, submitted, next_action,
                        next_due_at, note, now, now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE job_applications SET source_observation_id = ?, stage = ?,
                        resume_variant = ?, apply_url_snapshot = ?,
                        company_snapshot = ?, title_snapshot = ?,
                        description_snapshot = ?, submitted_at = ?,
                        next_action = ?, next_due_at = ?, note = ?,
                        version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        source_observation_id, stage, resume, apply_url_snapshot,
                        company_snapshot, title_snapshot, description_snapshot,
                        submitted, next_action, next_due_at, note, now,
                        application_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO application_events(
                    id, application_id, event_type, from_stage, to_stage,
                    occurred_at, due_at, note, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), application_id, event_type, from_stage, stage,
                    event_at, next_due_at, note,
                    dumps({
                        "human_confirmed": stage == "submitted",
                        "resume_variant": resume,
                    }),
                ),
            )
            connection.execute(
                "UPDATE job_entities SET triage_state = ?, version = version + 1, updated_at = ? WHERE id = ?",
                (triage_state, now, job_id),
            )

        await self.database.transaction(operation)
        return await self.get_application(application_id)

    async def list_applications(
        self,
        opportunity_types: Iterable[str] = (),
        include_unclassified: bool = False,
    ) -> dict[str, Any]:
        selected_types = tuple(
            dict.fromkeys(value for value in opportunity_types if value in VALID_TYPES)
        )
        type_values = (
            *selected_types,
            *(() if not include_unclassified else ("unclassified", "review")),
        )
        where = ""
        params: tuple[Any, ...] = ()
        if type_values:
            where = f"WHERE j.opportunity_type IN ({','.join('?' for _ in type_values)})"
            params = tuple(type_values)
        rows = await self.database.fetch_all(
            f"""
            SELECT a.*, j.opportunity_type, j.score, j.score_band, j.location
            FROM job_applications a
            JOIN job_entities j ON j.id = a.job_entity_id
            {where}
            ORDER BY
                CASE a.stage
                  WHEN 'offer' THEN 1 WHEN 'interview' THEN 2 WHEN 'assessment' THEN 3
                  WHEN 'screen' THEN 4 WHEN 'submitted' THEN 5 ELSE 6 END,
                CASE WHEN a.next_due_at IS NULL THEN 1 ELSE 0 END,
                a.next_due_at, a.updated_at DESC
            """,
            params,
        )
        return {"applications": [self._application(row) for row in rows]}

    @staticmethod
    def _application(row: dict[str, Any]) -> dict[str, Any]:
        slug = row.get("opportunity_type") or "unclassified"
        return {
            "id": row["id"],
            "job_id": row["job_entity_id"],
            "company": row["company_snapshot"],
            "title": row["title_snapshot"],
            "stage": row["stage"],
            "opportunity_type": TYPE_LABELS.get(slug, ""),
            "opportunity_type_slug": slug,
            "needs_classification": slug in {"unclassified", "review"},
            "location": row.get("location"),
            "score": row.get("score"),
            "score_band": row.get("score_band"),
            "resume_variant": row["resume_variant"],
            "source_observation_id": row.get("source_observation_id"),
            "apply_url_snapshot": row.get("apply_url_snapshot"),
            "description_snapshot": row.get("description_snapshot"),
            "submitted_at": row.get("submitted_at"),
            "next_action": row.get("next_action"),
            "next_due_at": row.get("next_due_at"),
            "note": row.get("note"),
            "updated_at": row.get("updated_at"),
        }

    async def get_application(self, application_id: str) -> dict[str, Any] | None:
        row = await self.database.fetch_one(
            """
            SELECT a.*, j.opportunity_type, j.score, j.score_band, j.location
            FROM job_applications a JOIN job_entities j ON j.id = a.job_entity_id
            WHERE a.id = ?
            """,
            (application_id,),
        )
        if row is None:
            return None
        result = self._application(row)
        result["events"] = await self.database.fetch_all(
            "SELECT * FROM application_events WHERE application_id = ? ORDER BY occurred_at DESC",
            (application_id,),
        )
        return result

    async def add_application_event(
        self,
        application_id: str,
        *,
        event_type: str,
        stage: str | None = None,
        note: str = "",
        occurred_at: str | None = None,
        due_at: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any] | None:
        aliases = {
            "saved": "considering",
            "queued": "preparing",
            "applied": "submitted",
        }
        stage = aliases.get((stage or "").casefold(), (stage or "").casefold()) or None
        if stage is not None and stage not in VALID_STAGES:
            raise ValueError("unknown application stage")
        at = occurred_at or utc_now()
        updated_at = utc_now()

        def operation(connection: sqlite3.Connection) -> bool:
            current = connection.execute(
                "SELECT stage, submitted_at FROM job_applications WHERE id = ?",
                (application_id,),
            ).fetchone()
            if current is None:
                return False
            if (
                stage == "submitted" or event_type == "submitted"
            ) and current["stage"] != "submitted":
                raise ValueError(
                    "use the human-confirmed submission endpoint to record submission"
                )
            downstream = {"assessment", "screen", "interview", "offer", "no_reply"}
            if stage in downstream or event_type in downstream:
                submission_events = connection.execute(
                    """
                    SELECT data_json FROM application_events
                    WHERE application_id = ?
                      AND event_type = 'submitted'
                      AND to_stage = 'submitted'
                    """,
                    (application_id,),
                ).fetchall()
                has_confirmed_submission = current["submitted_at"] is not None and any(
                    bool(loads(row["data_json"], {}).get("human_confirmed"))
                    for row in submission_events
                )
                if not has_confirmed_submission:
                    raise ValueError(
                        "post-submission stages require a prior human-confirmed submitted event"
                    )
            connection.execute(
                """
                INSERT INTO application_events(
                    id, application_id, event_type, from_stage, to_stage,
                    occurred_at, due_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id(), application_id, event_type, current["stage"], stage, at, due_at, note),
            )
            effective_stage = stage or str(current["stage"])
            supplied_due_action = note or f"{(stage or event_type).replace('_', ' ').title()} due"
            connection.execute(
                """
                UPDATE job_applications SET
                    stage = COALESCE(?, stage),
                    next_action = CASE
                        WHEN COALESCE(?, stage) IN ('rejected', 'withdrawn', 'no_reply') THEN NULL
                        WHEN ? IS NOT NULL THEN ?
                        WHEN ? = 'task_added' THEN ? ELSE next_action END,
                    next_due_at = CASE
                        WHEN COALESCE(?, stage) IN ('rejected', 'withdrawn', 'no_reply') THEN NULL
                        WHEN ? IS NOT NULL THEN ?
                        WHEN ? = 'task_added' THEN ? ELSE next_due_at END,
                    note = CASE WHEN ? != '' THEN ? ELSE note END,
                    version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    stage,
                    effective_stage,
                    due_at,
                    supplied_due_action,
                    event_type,
                    note,
                    effective_stage,
                    due_at,
                    due_at,
                    event_type,
                    due_at,
                    note,
                    note,
                    updated_at,
                    application_id,
                ),
            )
            return True

        changed = await self.database.transaction(operation)
        if not changed:
            return None
        return await self.get_application(application_id)

    async def get_profile(self) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT document_json FROM profile WHERE active = 1 LIMIT 1"
        )
        if row:
            return loads(row["document_json"], {})
        default = {
            "graduation_date": "2027-12",
            "citizenship": "US",
            "home_location": "San Diego, CA",
            "preferred_segments": ["ml_systems", "systems", "quant_dev", "backend_infra"],
            "relocate_for_internship": True,
            "reduced_enrollment": False,
        }
        await self.update_profile(default)
        return default

    async def update_profile(self, document: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        await self.database.execute(
            """
            INSERT INTO profile(name, document_json, active, created_at, updated_at)
            VALUES ('default', ?, 1, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                document_json = excluded.document_json,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (dumps(document), now, now),
        )
        return document
