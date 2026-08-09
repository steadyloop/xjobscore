"""Populate a demo database by running the real pipeline, then topping up.

Three passes, in order:

* **A — first fetch.**  Every demo feed is enabled and fetched through
  ``ManualFetchCoordinator`` and ``BoardIngestor``.  Nothing here fabricates a
  read-model row: entities, revisions, identifiers, dedupe candidates, merge
  operations, lifecycle events and snapshots are all produced by the shipping
  code from the corpus in :mod:`.dataset`.
* **B — second fetch.**  The gateway advances a round.  Two postings change
  body and deadline (producing revision 2, which is what ``changed_recently``
  and the Saved review banner read) and one vanishes from a complete/closing
  feed, so the lifecycle reconciler marks it unlisted on its own.
* **C — user actions and top-up.**  Triage, applications and application
  events go through ``BoardRepository`` so their gates really run.  Only then
  does :mod:`.topup` write the handful of states the pipeline cannot reach.

Feeds are fetched sequentially rather than through ``trigger_many`` so the
survivor of the cross-vendor merge is deterministic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..board import BoardRepository
from ..db import Database
from ..ingest import BoardIngestor, IngestCounts
from ..scheduler import ManualFetchCoordinator
from .catalog import DemoGateway
from .dataset import DEMO_FEEDS, DEMO_PROFILE, vendor_names

__all__ = ["SeedReport", "seed", "seed_if_empty", "build_coordinator", "reset_path"]


@dataclass
class SeedReport:
    rounds: int = 0
    fetches: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    jobs: int = 0
    applications: int = 0
    dedupe_candidates: int = 0
    topup: dict[str, int] = field(default_factory=dict)

    def add(self, counts: IngestCounts) -> None:
        for name, value in asdict(counts).items():
            self.counts[name] = self.counts.get(name, 0) + int(value)


def _iso(delta_days: float = 0, *, hours: float = 0) -> str:
    moment = datetime.now(timezone.utc) + timedelta(days=delta_days, hours=hours)
    return moment.isoformat(timespec="microseconds")


async def _fetch_round(
    coordinator: ManualFetchCoordinator,
    ingestor: BoardIngestor,
    report: SeedReport,
    feed_ids: tuple[int, ...],
) -> None:
    for feed_id in feed_ids:
        result = await coordinator.trigger(feed_id)
        report.fetches += 1
        report.add(await ingestor.ingest(result))
    report.rounds += 1


async def _apply_triage(board: BoardRepository, jobs: dict[str, str]) -> None:
    """Set triage on the named external ids, ignoring ones a gate refuses."""
    for external_id, state in jobs.items():
        job_id = await _job_id_for(board, external_id)
        if job_id is None:
            continue
        try:
            await board.set_triage(job_id, state)
        except ValueError:
            # A gate refused (queue requires open + eligible). The demo should
            # show the gate working, not route around it.
            continue


async def _job_id_for(board: BoardRepository, external_id: str) -> str | None:
    row = await board.database.fetch_one(
        """
        SELECT job_entity_id FROM posting_observations
        WHERE external_id = ? ORDER BY id LIMIT 1
        """,
        (external_id,),
    )
    if row is None:
        return None
    # Follow a merge so the caller always addresses a surviving entity.
    entity = await board.database.fetch_one(
        "SELECT id, status, merged_into_id FROM job_entities WHERE id = ?",
        (row["job_entity_id"],),
    )
    if entity is None:
        return None
    if entity["status"] == "merged" and entity["merged_into_id"]:
        return str(entity["merged_into_id"])
    return str(entity["id"])


# external_id -> (opening stage, follow-up stage or None, next-due offset in days)
_APPLICATION_PLAN: tuple[tuple[str, str, str | None, float | None, str], ...] = (
    ("bh-clinical", "considering", None, 6, "Decide whether the trial-data scope is worth a slot"),
    ("nr-9114", "preparing", None, 1, "Draft the research-statement paragraph"),
    ("NR_5510_WD", "submitted", None, None, ""),
    ("hs-rel-a1", "submitted", "assessment", 2, "Take-home assessment window closes"),
    ("hs-rel-b2", "submitted", "screen", 9, "Recruiter screen scheduled"),
    ("ml-spring-coop", "submitted", "interview", 10, "Onsite loop with the manipulation team"),
    ("ag-sre-gh", "submitted", "offer", 11, "Offer decision deadline"),
    ("ag-sre-sr", "submitted", "rejected", None, "Duplicate requisition; the Greenhouse one advanced"),
    ("cf-clearance", "submitted", "withdrawn", None, "Withdrew: clearance sponsorship not offered"),
    ("nr-8802", "submitted", "no_reply", None, "No response after two follow-ups"),
)


async def _apply_applications(board: BoardRepository, report: SeedReport) -> None:
    for external_id, stage, follow_up, due_days, note in _APPLICATION_PLAN:
        job_id = await _job_id_for(board, external_id)
        if job_id is None:
            continue
        # A submission recorded in the past is what gives the follow-up task a
        # due date inside Today's seven-day window.
        submitted_at = _iso(-12) if follow_up == "assessment" else _iso(-2)
        try:
            application = await board.create_application(
                job_id,
                stage=stage,
                submitted_at=submitted_at if stage == "submitted" else None,
                note=note if follow_up is None else "",
            )
        except ValueError:
            continue
        if application is None:
            continue
        report.applications += 1
        if follow_up is None:
            continue
        await board.add_application_event(
            application["id"],
            event_type=follow_up,
            stage=follow_up,
            note=note,
            occurred_at=_iso(-1),
            due_at=_iso(due_days) if due_days is not None else None,
            confirmed=True,
        )


async def seed(
    database: Database,
    coordinator: ManualFetchCoordinator,
    *,
    ingestor: BoardIngestor | None = None,
    board: BoardRepository | None = None,
) -> SeedReport:
    """Fill ``database`` with the demo corpus. Safe to call on a fresh file."""
    from .topup import apply_topup

    await database.initialize()
    ingestor = ingestor or BoardIngestor(database)
    board = board or BoardRepository(database)
    report = SeedReport()

    # The profile is an eligibility input, so it has to exist before ingest.
    await board.update_profile(dict(DEMO_PROFILE))

    for source in vendor_names():
        await board.set_vendor_selection(source, True)

    ordered = tuple(feed.id for feed in DEMO_FEEDS)
    gateway = coordinator.gateway

    # Pass A.
    await _fetch_round(coordinator, ingestor, report, ordered)

    # Pass B: only the feeds whose corpus actually changes.
    if isinstance(gateway, DemoGateway):
        gateway.round_index = 1
    await _fetch_round(coordinator, ingestor, report, (9001, 9003, 9007))

    # Pass C, part one: user actions through their real gates.
    await _apply_triage(
        board,
        {
            "hs-quant-01": "saved",
            "ml-zurich": "saved",
            "cf-embedded": "queued",
            "vq-exp": "dismissed",
        },
    )
    await _apply_applications(board, report)

    # A human classification correction is the only writer of
    # manual_opportunity_type / manual_classification_evidence.
    unclassified_id = await _job_id_for(board, "nr-7300")
    if unclassified_id is not None:
        await board.set_classification(
            unclassified_id,
            "summer_internship",
            "Recruiter confirmed a June-August term for the 2027 cohort.",
        )

    # Pass C, part two: everything the pipeline provably cannot emit.
    report.topup = await apply_topup(database)

    totals = await database.fetch_one(
        """
        SELECT (SELECT COUNT(*) FROM job_entities WHERE status != 'merged') AS jobs,
               (SELECT COUNT(*) FROM job_applications) AS applications,
               (SELECT COUNT(*) FROM dedupe_candidates) AS dedupe
        """
    )
    if totals:
        report.jobs = int(totals["jobs"])
        report.applications = int(totals["applications"])
        report.dedupe_candidates = int(totals["dedupe"])
    return report


async def seed_if_empty(
    database: Database, coordinator: ManualFetchCoordinator
) -> SeedReport | None:
    """Seed only when the board is empty, so a restart never duplicates work."""
    await database.initialize()
    row = await database.fetch_one("SELECT COUNT(*) AS total FROM job_entities")
    if row and int(row["total"]) > 0:
        # An already-seeded database is at the final round. Rewinding the
        # gateway would make the next manual fetch look like a reversion.
        gateway = coordinator.gateway
        if isinstance(gateway, DemoGateway):
            gateway.round_index = gateway.final_round
        return None
    return await seed(database, coordinator)


def build_coordinator(database: Database) -> ManualFetchCoordinator:
    """A coordinator wired to the offline demo gateway."""
    return ManualFetchCoordinator(
        database,
        DemoGateway(),  # type: ignore[arg-type]  # duck-typed gateway seam
        max_concurrency=4,
        max_selection=25,
    )


def reset_path(path: Path) -> None:
    """Delete a demo database and its WAL sidecars."""
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


async def seed_fresh(path: Path) -> SeedReport:
    """Drop ``path`` and rebuild it from scratch."""
    reset_path(path)
    database = Database(path)
    coordinator = build_coordinator(database)
    await coordinator.initialize()
    try:
        return await seed(database, coordinator)
    finally:
        await coordinator.close()


def summarize(report: SeedReport) -> str:
    lines = [
        f"rounds={report.rounds} fetches={report.fetches}",
        "ingested: "
        + ", ".join(f"{name}={value}" for name, value in sorted(report.counts.items())),
        f"jobs={report.jobs} applications={report.applications} "
        f"dedupe_candidates={report.dedupe_candidates}",
    ]
    if report.topup:
        lines.append(
            "top-up: "
            + ", ".join(f"{name}={value}" for name, value in sorted(report.topup.items()))
        )
    return "\n".join(lines)
