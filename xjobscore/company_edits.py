"""Pull administrator edits back from OneShot before publication.

The queue remains on the target until a successful publication acknowledges
the exact revision. Source updates are idempotent across retries and outages.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)
FIELDS = ("name", "domain", "description", "tags")
COMPANY_KEY = "CASE WHEN c.domain IS NOT NULL THEN jsonb_build_array('domain', lower(c.domain)) ELSE jsonb_build_array('source_id', c.id, c.name) END"


def _changes(value: Any) -> dict[str, Any]:
    changes = json.loads(value) if isinstance(value, str) else value
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
        raise ValueError("Invalid company fields")
    if "name" in changes and (not isinstance(changes["name"], str) or not changes["name"].strip()):
        raise ValueError("Company name is required")
    if "tags" in changes and not isinstance(changes["tags"], dict):
        raise ValueError("Tags must be a JSON object")
    for field in ("domain", "description"):
        if field in changes and changes[field] is not None and not isinstance(changes[field], str):
            raise ValueError(f"{field} must be text or null")
    return changes


async def sync(source: Any, target: Any, *, dry_run: bool = False) -> dict[int, Any]:
    """Return revisions safe to acknowledge after publishing this snapshot."""
    if dry_run or not await target.fetchval("SELECT to_regclass('company_edit')"):
        return {}
    revisions = {}
    ids = await target.fetch("SELECT company_id FROM company_edit ORDER BY updated_at, company_id")
    for item in ids:
        async with target.transaction():
            edit = await target.fetchrow(
                "SELECT * FROM company_edit WHERE company_id = $1 FOR UPDATE", item["company_id"]
            )
            if edit is None:
                continue
            try:
                changes = _changes(edit["changes"])
                async with source.transaction():
                    # Use the identity allocator's lock. Renaming a domainless
                    # company or changing its domain must keep its serving ID.
                    await source.execute("SELECT pg_advisory_xact_lock(627031007)")
                    company = await source.fetchrow(
                        f"SELECT c.id FROM company c JOIN frontend_identity m "
                        f"ON m.kind = 'company' AND m.key = {COMPANY_KEY} "
                        "WHERE m.id = $1 FOR UPDATE OF c", edit["company_id"],
                    )
                    if company is None:
                        raise ValueError("The source company no longer exists or its identity has changed")
                    values = [company["id"]]
                    assignments = []
                    for field in FIELDS:
                        if field in changes:
                            values.append(changes[field])
                            cast = "::jsonb" if field == "tags" else ""
                            assignments.append(f"{field} = ${len(values)}{cast}")
                    await source.execute(
                        f"UPDATE company SET {', '.join(assignments)} WHERE id = $1", *values
                    )
                    await source.execute(
                        f"UPDATE frontend_identity m SET previous_keys = "
                        "CASE WHEN NOT (m.previous_keys @> jsonb_build_array(m.key)) "
                        "THEN m.previous_keys || jsonb_build_array(m.key) ELSE m.previous_keys END, "
                        f"key = {COMPANY_KEY} FROM company c "
                        "WHERE m.kind = 'company' AND m.id = $1 AND c.id = $2",
                        edit["company_id"], company["id"],
                    )
                # The source commits first. A crash leaves an idempotent queue
                # entry to replay, rather than acknowledging an unsaved change.
                await target.execute(
                    "UPDATE company_edit SET sync_error = NULL WHERE company_id = $1", edit["company_id"]
                )
                revisions[int(edit["company_id"])] = edit["revision"]
            except Exception as exc:
                # The source savepoint rolled back. Keep the edit visible and
                # allow unrelated company/job publication to continue.
                logger.warning("Company edit %s could not sync: %s", edit["company_id"], exc)
                detail = "That domain or company identity is already in use" if getattr(exc, "sqlstate", "") == "23505" else str(exc)
                await target.execute(
                    "UPDATE company_edit SET sync_error = $2 WHERE company_id = $1",
                    edit["company_id"], detail[:1000],
                )
    return revisions
