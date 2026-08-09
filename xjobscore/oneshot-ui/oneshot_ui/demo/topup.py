"""Fill the states the live pipeline provably cannot produce.

Everything written here is either (a) unreachable through ``prepare_posting``
and ``BoardIngestor`` — the ``one_shot`` band needs a score of 90 and the
pipeline's ceiling is 82 — or (b) a column no code path in the application ever
writes, such as ``companies.domain``.

Each block says which of the two it is.  If a future change makes one of these
reachable for real, delete the block rather than leaving both.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..db import Database
from ..util import dumps, new_id, public_id, utc_now

__all__ = ["apply_topup"]


def _iso(days: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(
        timespec="microseconds"
    )


# Domains are never written by any code path in the application.
_COMPANY_DOMAINS = {
    "Northwind Robotics": "northwindrobotics.example",
    "Halcyon Systems": "halcyonsystems.example",
    "Meridian Labs": "meridianlabs.example",
    "Borealis Health": "borealishealth.example",
    "Vantage Quant": "vantagequant.example",
    "Atlas Grid": "atlasgrid.example",
    "Cobalt Foundry": "cobaltfoundry.example",
    "Vertex MedTech": "vertexmedtech.example",
}

_ONE_SHOT_COMPONENTS = [
    {"name": "segment_fit", "points": 30, "maximum": 30, "explanation": "segment 1"},
    {"name": "conversion_probability", "points": 25, "maximum": 25,
     "explanation": "named return-offer conversion rate"},
    {"name": "window_fit", "points": 15, "maximum": 15, "explanation": "exact"},
    {"name": "experience_value", "points": 15, "maximum": 15,
     "explanation": "code ownership, named team, and target stack"},
    {"name": "latency_advantage", "points": 6, "maximum": 10,
     "explanation": "review cadence and applicant-flow advantage"},
    {"name": "compensation", "points": 3, "maximum": 5,
     "explanation": "compensation is a tiebreaker"},
]
_ONE_SHOT_ROUTE = (
    "Apply once per cycle at open with tailored materials and a referral; "
    "never auto-submit."
)


async def apply_topup(database: Database) -> dict[str, int]:
    """Apply every top-up block and report how many rows each one wrote."""
    now = utc_now()

    def operation(connection: sqlite3.Connection) -> dict[str, int]:
        written: dict[str, int] = {}

        def note(name: str, cursor: sqlite3.Cursor) -> None:
            written[name] = written.get(name, 0) + max(0, cursor.rowcount)

        def entity_by_external(external_id: str) -> str | None:
            row = connection.execute(
                """
                SELECT e.id FROM posting_observations o
                JOIN job_entities e ON e.id = o.job_entity_id
                WHERE o.external_id = ? AND e.status != 'merged'
                ORDER BY o.id LIMIT 1
                """,
                (external_id,),
            ).fetchone()
            return str(row["id"]) if row else None

        # -- companies.domain -------------------------------------------
        # Unwritten column: nothing in the app ever sets a company domain.
        for display_name, domain in _COMPANY_DOMAINS.items():
            note(
                "companies.domain",
                connection.execute(
                    "UPDATE companies SET domain = ?, updated_at = ? WHERE display_name = ?",
                    (domain, now, display_name),
                ),
            )

        # -- job_entities.workplace_type --------------------------------
        # Most parsers leave workplace_type_raw unset, so the column is
        # normally NULL. Backfill from the observation that carries it.
        note(
            "job_entities.workplace_type",
            connection.execute(
                """
                UPDATE job_entities SET workplace_type = (
                    SELECT o.workplace_type FROM posting_observations o
                    WHERE o.job_entity_id = job_entities.id
                      AND o.workplace_type IS NOT NULL
                    ORDER BY o.id LIMIT 1
                )
                WHERE workplace_type IS NULL
                """
            ),
        )

        # -- the one_shot band ------------------------------------------
        # Unreachable: prepare_posting's maximum total is 82 and the band
        # threshold is 90, so no harvested posting can ever route one_shot.
        one_shot_id = entity_by_external("vq-summer")
        if one_shot_id:
            note(
                "job_entities.one_shot",
                connection.execute(
                    """
                    UPDATE job_entities SET
                        score = 94, score_band = 'one_shot',
                        score_components_json = ?, route_instruction = ?,
                        submit_within_hours = NULL, version = version + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (dumps(_ONE_SHOT_COMPONENTS), _ONE_SHOT_ROUTE, now, one_shot_id),
                ),
            )
            note(
                "score_snapshots",
                connection.execute(
                    """
                    INSERT INTO score_snapshots(
                        id, job_entity_id, score, band, components_json,
                        resume_variant, algorithm_version, created_at, route_json
                    ) VALUES (?, ?, 94, 'one_shot', ?, 'quant-systems',
                              'opportunity-score/1.0', ?, ?)
                    """,
                    (
                        new_id(), one_shot_id, dumps(_ONE_SHOT_COMPONENTS), now,
                        dumps({
                            "band": "one_shot",
                            "submit_within_hours": None,
                            "auto_submit": False,
                            "instruction": _ONE_SHOT_ROUTE,
                        }),
                    ),
                ),
            )

        # -- the 'conflicting' entity status ----------------------------
        # Reachable only when two authoritative sources disagree about
        # closure within one entity; this corpus has no such pair.
        conflicting_id = entity_by_external("hs-rel-b2")
        if conflicting_id:
            note(
                "job_entities.conflicting",
                connection.execute(
                    """
                    UPDATE job_entities SET status = 'conflicting',
                        version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, conflicting_id),
                ),
            )
            note(
                "lifecycle_events",
                connection.execute(
                    """
                    INSERT INTO lifecycle_events(
                        id, observation_id, job_entity_id, from_status, to_status,
                        reason, poll_id, created_at, algorithm_version, details_json
                    ) VALUES (?, NULL, ?, 'open', 'conflicting',
                              'canonical employer listing and aggregator copy disagree',
                              NULL, ?, 'posting-lifecycle/1.0', ?)
                    """,
                    (
                        new_id(), conflicting_id, now,
                        dumps({"sources": ["lever:halcyon-systems"], "resolved": False}),
                    ),
                ),
            )

        # -- link_evidence review states --------------------------------
        # The ingestor only ever writes 'accepted'; 'pending' and 'rejected'
        # come from a human review queue that is not built.
        observations = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM posting_observations ORDER BY id LIMIT 4"
            ).fetchall()
        ]
        if len(observations) >= 4:
            for left, right, state, method, confidence, reason in (
                (observations[0], observations[2], "pending", "title_similarity", 0.71,
                 "titles are close but no scoped identifier agrees"),
                (observations[1], observations[3], "rejected", "title_similarity", 0.58,
                 "reviewed and rejected: different requisitions"),
            ):
                pair = sorted((left, right))
                note(
                    "link_evidence",
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO link_evidence(
                            id, observation_a_id, observation_b_id, method,
                            confidence, details_json, algorithm_version,
                            review_state, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'posting-resolution/1.0', ?, ?)
                        """,
                        (
                            public_id("link", *pair), pair[0], pair[1], method,
                            confidence, dumps({"reason": reason}), state, now,
                        ),
                    ),
                )

        # -- dedupe states the corpus does not reach --------------------
        # Ingest emits 'merged', 'pending' and 'related'. A human queue would
        # produce the rest; this writes one of each so the review UI shows
        # every card variant.
        entities = [
            str(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM job_entities WHERE status != 'merged'
                ORDER BY created_at, id LIMIT 8
                """
            ).fetchall()
        ]
        extra_states = (
            ("deferred", "defer", 0.62, "same employer, unclear whether the terms differ"),
            ("kept_separate", "keep_separate", 0.66, "two genuinely distinct requisitions"),
            ("reversed", "reverse", 0.94, "merged in error; the roles are on different teams"),
        )
        for index, (state, action, confidence, reason) in enumerate(extra_states):
            left_index, right_index = index * 2, index * 2 + 1
            if right_index >= len(entities):
                break
            left, right = entities[left_index], entities[right_index]
            candidate_id = public_id("dedupe", left, right)
            note(
                "dedupe_candidates",
                connection.execute(
                    """
                    INSERT OR IGNORE INTO dedupe_candidates(
                        id, entity_a_id, entity_b_id, outcome, confidence,
                        reasons_json, differences_json, state, created_at,
                        updated_at, algorithm_version, shared_identifiers_json
                    ) VALUES (?, ?, ?, 'review', ?, ?, ?, ?, ?, ?,
                              'posting-resolution/1.0', ?)
                    """,
                    (
                        candidate_id, left, right, confidence, dumps([reason]),
                        dumps({"auto_merge": False}), state, now, now, dumps([]),
                    ),
                ),
            )
            note(
                "dedupe_decisions",
                connection.execute(
                    """
                    INSERT INTO dedupe_decisions(
                        id, candidate_id, action, rationale, actor, created_at
                    ) VALUES (?, ?, ?, ?, 'human', ?)
                    """,
                    (new_id(), candidate_id, action, reason, now),
                ),
            )
            if state == "reversed":
                note(
                    "merge_operations",
                    connection.execute(
                        """
                        INSERT INTO merge_operations(
                            id, candidate_id, survivor_id, absorbed_id,
                            moved_observation_ids_json, created_at, reversed_at,
                            moved_application_ids_json, before_state_json
                        ) VALUES (?, ?, ?, ?, '[]', ?, ?, '[]', ?)
                        """,
                        (
                            new_id(), candidate_id, left, right, _iso(-1), now,
                            dumps({"note": "restored from the pre-merge snapshot"}),
                        ),
                    ),
                )

        # -- feed health states -----------------------------------------
        # 'quarantined' needs a >=80% membership drop from a >=10-item
        # baseline. Manufacturing that would mean seeding ten throwaway
        # postings, so the state is written directly instead.
        note(
            "feeds.quarantined",
            connection.execute(
                """
                UPDATE feeds SET snapshot_state = 'quarantined',
                    snapshot_reason = 'membership dropped 86% from a 14-item baseline',
                    health = 'partial', last_outcome = 'partial', updated_at = ?
                WHERE source = 'smartrecruiters' AND feed_key = 'VantageQuant'
                """,
                (now,),
            ),
        )
        note(
            "feeds.missing_parser",
            connection.execute(
                """
                UPDATE feeds SET parser_state = 'missing', health = 'missing_parser',
                    last_error = 'no parser is registered for this transport',
                    updated_at = ?
                WHERE source = 'workable'
                """,
                (now,),
            ),
        )

        # -- bronze payload_text ----------------------------------------
        # Every demo adapter returns JSON, so payload_text stays NULL. A real
        # document board stores text; attach one to the failed poll.
        failed_poll = connection.execute(
            """
            SELECT p.id, p.feed_id FROM polls p JOIN feeds f ON f.id = p.feed_id
            WHERE f.source = 'icims' ORDER BY p.id DESC LIMIT 1
            """
        ).fetchone()
        if failed_poll is not None:
            note(
                "observations.payload_text",
                connection.execute(
                    """
                    INSERT OR IGNORE INTO observations(
                        poll_id, feed_id, external_id, kind, payload_json,
                        payload_text, envelope_json, observed_at
                    ) VALUES (?, ?, 'vertex-error-body', 'html', NULL, ?, ?, ?)
                    """,
                    (
                        failed_poll["id"], failed_poll["feed_id"],
                        "<html><body><h1>503 Service Unavailable</h1>"
                        "<p>The iCIMS portal is temporarily unavailable.</p></body></html>",
                        dumps({"retry_after": 900}), now,
                    ),
                ),
            )

        # -- settings ----------------------------------------------------
        for key, value in (
            ("demo_mode", True),
            ("board_timezone", "America/Los_Angeles"),
            ("weekly_application_target", 12),
        ):
            note(
                "settings",
                connection.execute(
                    """
                    INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, dumps(value), now),
                ),
            )

        written.update(_seed_legacy_tables(connection, now, entities))
        return written

    return await database.transaction(operation)


def _seed_legacy_tables(
    connection: sqlite3.Connection, now: str, entities: list[str]
) -> dict[str, int]:
    """Populate the migration-001 tables that no current code path reads.

    ``board.py`` and ``ingest.py`` moved to the migration-002 read model and
    never touch these again, but they are still real tables in the schema, so
    each gets one representative row rather than staying empty.
    """
    written: dict[str, int] = {}

    def note(name: str, cursor: sqlite3.Cursor) -> None:
        written[name] = written.get(name, 0) + max(0, cursor.rowcount)

    def insert(name: str, sql: str, params: tuple[object, ...]) -> int:
        cursor = connection.execute(sql, params)
        note(name, cursor)
        return int(cursor.lastrowid or 0)

    # Every one of these tables uses INTEGER AUTOINCREMENT keys, unlike the
    # migration-002 read model's TEXT uuids.
    entity_id = insert(
        "legacy.entities",
        """
        INSERT INTO entities(kind, canonical_name, status,
                             attributes_json, created_at, updated_at)
        VALUES ('company', 'Northwind Robotics', 'active', ?, ?, ?)
        """,
        (dumps({"legacy": True, "superseded_by": "companies"}), now, now),
    )
    insert(
        "legacy.identifiers",
        """
        INSERT INTO identifiers(entity_id, namespace, value, is_primary, created_at)
        VALUES (?, 'legacy_domain', 'northwindrobotics.example', 1, ?)
        """,
        (entity_id, now),
    )
    observation = connection.execute(
        "SELECT id FROM observations ORDER BY id LIMIT 1"
    ).fetchone()
    revision_id = insert(
        "legacy.revisions",
        """
        INSERT INTO revisions(entity_id, observation_id, revision_no,
                              content_hash, document_json, created_at)
        VALUES (?, ?, 1, 'legacy-revision-hash', ?, ?)
        """,
        (entity_id, observation["id"] if observation else None, dumps({"legacy": True}), now),
    )
    insert(
        "legacy.memberships",
        """
        INSERT INTO memberships(parent_entity_id, child_entity_id,
                                relationship, valid_from, valid_to, attributes_json)
        VALUES (?, ?, 'subsidiary', ?, ?, ?)
        """,
        (entity_id, entity_id, now, _iso(365), dumps({"legacy": True})),
    )
    insert(
        "legacy.evidence",
        """
        INSERT INTO evidence(entity_id, revision_id, kind, value_json,
                             provenance, confidence, source_span_json, created_at)
        VALUES (?, ?, 'headcount', ?, 'source', 0.9, ?, ?)
        """,
        (entity_id, revision_id, dumps(480), dumps({"start": 0, "end": 12}), now),
    )
    candidate_id = insert(
        "legacy.candidates",
        """
        INSERT INTO candidates(entity_id, stage, status, discovered_at, updated_at)
        VALUES (?, 'triaged', 'active', ?, ?)
        """,
        (entity_id, now, now),
    )
    insert(
        "legacy.decisions",
        """
        INSERT INTO decisions(candidate_id, decision, reason, actor, decided_at)
        VALUES (?, 'keep', 'legacy read model retained for replay', 'human', ?)
        """,
        (candidate_id, now),
    )
    profile_row = connection.execute("SELECT id FROM profile ORDER BY id LIMIT 1").fetchone()
    insert(
        "legacy.requirements",
        """
        INSERT INTO requirements(profile_id, name, operator, value_json,
                                 weight, required, enabled, created_at, updated_at)
        VALUES (?, 'min_hourly_rate', '>=', ?, 3, 0, 1, ?, ?)
        """,
        (profile_row["id"] if profile_row else None, dumps(40), now, now),
    )
    insert(
        "legacy.classification",
        """
        INSERT INTO classification(candidate_id, label, value_json,
                                   provenance, confidence, created_at)
        VALUES (?, 'opportunity_type', ?, 'model', 0.75, ?)
        """,
        (candidate_id, dumps("summer_internship"), now),
    )
    insert(
        "legacy.scores",
        """
        INSERT INTO scores(candidate_id, model, total, components_json, scored_at)
        VALUES (?, 'opportunity-score/0.9', 71, ?, ?)
        """,
        (candidate_id, dumps({"segment_fit": 25}), now),
    )
    insert(
        "legacy.triage",
        """
        INSERT INTO triage(candidate_id, bucket, rank, pinned, note, updated_at)
        VALUES (?, 'shortlist', 1, 1, 'legacy triage row', ?)
        ON CONFLICT(candidate_id) DO NOTHING
        """,
        (candidate_id, now),
    )
    insert(
        "legacy.applications",
        """
        INSERT INTO applications(candidate_id, status, applied_at,
                                 external_url, data_json, created_at, updated_at)
        VALUES (?, 'submitted', ?, 'https://jobs.example.test/legacy', ?, ?, ?)
        """,
        (candidate_id, _iso(-30), dumps({"legacy": True}), now, now),
    )
    if entities:
        insert(
            "legacy.events",
            """
            INSERT INTO events(aggregate_type, aggregate_id, event_type,
                               data_json, occurred_at)
            VALUES ('demo', ?, 'demo_seeded', ?, ?)
            """,
            (entities[0], dumps({"source": "oneshot_ui.demo", "pass": "topup"}), now),
        )
    return written
