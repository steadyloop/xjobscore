"""Read-only closure analytics. Cache expensive fleet reads for one minute."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import timedelta
import time
from zoneinfo import ZoneInfo

AGES = ("< 1h", "1–6h", "6–24h", "1–3d", "3d+")
REASONS = ("ats", "dead", "message", "redirect", "page_unknown", "unknown")


def bucket(hours):
    return next((i for i, bound in enumerate((1, 6, 24, 72)) if hours < bound), 4)


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def prepare(row):
    row = dict(row)
    signals = {k: row[k] for k in ("ats_closed_at", "linked_dead_at", "paged_closed_at") if row[k]}
    row["detected_at"] = min(signals.values(), default=None)
    first = min(signals, key=signals.get) if signals else None
    row["reason"] = {"ats_closed_at": "ats", "linked_dead_at": "dead"}.get(first, "unknown")
    if first == "paged_closed_at":
        row["reason"] = ("redirect" if row.get("detail") == "job page redirected"
                         else "message" if row.get("detail") else "page_unknown")
    # Reappearance is a review lead, not a verdict that a closure was wrong.
    row["review"] = bool(row["detected_at"] and row["ats_closed_at"] is None
                         and row["missing_since"] is None and row["last_seen_at"] > row["detected_at"])
    lag = ((row["detected_at"] - row["missing_since"]).total_seconds() / 3600
           if row["detected_at"] and row["missing_since"] else None)
    row["lag_hours"] = lag if lag is not None and lag >= 0 else None
    row["vendor"] = row["source"].split(":", 1)[0]
    return row


class Analytics:
    def __init__(self):
        self._cached = None
        self._loaded_at = 0
        self._lock = asyncio.Lock()

    async def snapshot(self, pool):
        async with self._lock:
            if self._cached is not None and time.monotonic() - self._loaded_at < 60:
                return self._cached
            async with pool.acquire() as conn, conn.transaction(readonly=True, isolation="repeatable_read"):
                await conn.execute("SET LOCAL statement_timeout = '30s'")
                now = await conn.fetchval("SELECT now()")
                since = await conn.fetchval("SELECT min(first_seen_at) FROM job_posting")
                closed = await conn.fetch("""
                    WITH closed AS MATERIALIZED (
                        SELECT id,source,source_key,title,company_id,source_url,apply_url,
                               location_text,posted_at,first_seen_at,last_seen_at,missing_since,
                               ats_closed_at,linked_dead_at,paged_closed_at
                        FROM job_posting WHERE status='closed'
                          AND (LEAST(ats_closed_at,linked_dead_at,paged_closed_at) >= now()-interval '91 days'
                               OR LEAST(ats_closed_at,linked_dead_at,paged_closed_at) IS NULL)
                    )
                    SELECT j.id,j.source,j.title,c.name AS company,j.source_url,j.apply_url,
                           j.location_text,j.posted_at,j.first_seen_at,j.last_seen_at,
                           j.missing_since,j.ats_closed_at,j.linked_dead_at,j.paged_closed_at,
                           p.evidence->>'detail' AS detail,p.evidence->>'http_status' AS http_status,
                           p.evidence->>'url' AS checked_url,p.evidence->>'final_url' AS final_url
                    FROM closed j JOIN company c ON c.id=j.company_id
                    LEFT JOIN LATERAL (
                        SELECT r.data #> '{payload,_probe}' AS evidence
                        FROM raw_posting r
                        WHERE r.source=j.source AND r.source_key=j.source_key
                          AND r.data #>> '{payload,_probe,outcome}' = CASE
                              WHEN j.paged_closed_at=LEAST(j.ats_closed_at,j.linked_dead_at,j.paged_closed_at)
                                  THEN 'page_closed'
                              WHEN j.linked_dead_at=LEAST(j.ats_closed_at,j.linked_dead_at,j.paged_closed_at)
                                  THEN 'absent' END
                        ORDER BY r.fetched_at,r.id LIMIT 1
                    ) p ON true
                """)
                backlog = await conn.fetch("""
                    SELECT split_part(j.source,':',1) AS vendor,
                           coalesce(q.last_outcome,'unchecked') AS outcome,
                           CASE WHEN $1::timestamptz-j.missing_since < interval '1 hour' THEN 0
                                WHEN $1::timestamptz-j.missing_since < interval '6 hours' THEN 1
                                WHEN $1::timestamptz-j.missing_since < interval '24 hours' THEN 2
                                WHEN $1::timestamptz-j.missing_since < interval '3 days' THEN 3
                                ELSE 4 END AS age,
                           count(*) AS count
                    FROM job_posting j LEFT JOIN reconcile_queue q
                      ON q.posting_id=j.id AND q.missing_since=j.missing_since
                    WHERE j.status='active' AND j.missing_since IS NOT NULL
                    GROUP BY 1,2,3
                """, now)
            self._cached = {"observed_at": now, "history_since": since,
                            "closed": [prepare(r) for r in closed],
                            "backlog": [dict(r) for r in backlog]}
            self._loaded_at = time.monotonic()
            return self._cached


def window(snapshot, days, tz):
    now = snapshot["observed_at"]
    zone = ZoneInfo(tz)
    start = (now.astimezone(zone).date() - timedelta(days=days - 1))
    if snapshot["history_since"]:
        start = max(start, snapshot["history_since"].astimezone(zone).date())
    closed = [r for r in snapshot["closed"] if r["detected_at"]
              and start <= r["detected_at"].astimezone(zone).date()
              and r["detected_at"] <= now]
    return start, closed


def summary(snapshot, days, tz):
    now = snapshot["observed_at"]
    zone = ZoneInfo(tz)
    start, closed = window(snapshot, days, tz)
    daily = {}
    for i in range((now.astimezone(zone).date() - start).days + 1):
        daily[(start + timedelta(days=i)).isoformat()] = dict.fromkeys(REASONS, 0)
    for row in closed:
        daily[row["detected_at"].astimezone(zone).date().isoformat()][row["reason"]] += 1
    lags = [r["lag_hours"] for r in closed if r["lag_hours"] is not None]
    histogram = Counter(bucket(h) for h in lags)
    outcomes = Counter()
    vendors = defaultdict(lambda: {"total": 0, "ages": [0] * 5, "outcomes": Counter()})
    for row in snapshot["backlog"]:
        vendors[row["vendor"]]["total"] += row["count"]
        vendors[row["vendor"]]["ages"][row["age"]] += row["count"]
        vendors[row["vendor"]]["outcomes"][row["outcome"]] += row["count"]
        outcomes[row["outcome"]] += row["count"]
    return {
        "observed_at": now, "history_since": snapshot["history_since"], "timezone": tz,
        "days": days, "window_start": start,
        "metrics": {"closed": len(closed),
                    "closed_24h": sum(now - r["detected_at"] <= timedelta(days=1) for r in snapshot["closed"]
                                      if r["detected_at"] and r["detected_at"] <= now),
                    "unresolved": sum(outcomes.values()),
                    "over_24h": sum(r["count"] for r in snapshot["backlog"] if r["age"] >= 3),
                    "review": sum(r["review"] for r in closed),
                    "median_hours": percentile(lags, .5), "p95_hours": percentile(lags, .95),
                    "lag_sample": len(lags),
                    "undated": sum(r["detected_at"] is None for r in snapshot["closed"])},
        "daily": [{"day": day, "counts": counts} for day, counts in daily.items()],
        "evidence": dict(Counter(r["reason"] for r in closed)),
        "lag": [{"label": label, "count": histogram[i]} for i, label in enumerate(AGES)],
        "outcomes": dict(outcomes),
        "vendors": [{"vendor": name, **value} for name, value in
                    sorted(vendors.items(), key=lambda p: (-p[1]["total"], p[0]))],
    }


async def postings(pool, snapshot, *, days, tz, view, vendor=None, reason=None,
                   day=None, age=None, outcome=None, q="", offset=0, limit=25):
    if view != "unresolved":
        _, rows = window(snapshot, days, tz)
        zone = ZoneInfo(tz)
        rows = [r for r in rows if (view != "review" or r["review"])
                and (not vendor or r["vendor"] == vendor)
                and (not reason or r["reason"] == reason)
                and (not day or r["detected_at"].astimezone(zone).date().isoformat() == day)
                and (age is None or (r["lag_hours"] is not None and bucket(r["lag_hours"]) == age))
                and (not q or q.casefold() in " ".join((r["title"], r["company"], r["source"])).casefold())]
        rows.sort(key=lambda r: (r["detected_at"], r["id"]), reverse=True)
        return {"total": len(rows), "rows": rows[offset:offset + limit]}
    # Current missing episodes only; older/cancelled queue entries cannot inflate counts.
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute("SET LOCAL statement_timeout = '30s'")
        rows = await conn.fetch("""
            WITH missing AS (
                SELECT j.id,j.title,c.name AS company,j.source,j.source_url,j.apply_url,
                       j.location_text,j.first_seen_at,j.last_seen_at,j.missing_since,
                       q.last_checked_at,q.last_error,coalesce(q.last_outcome,'unchecked') AS outcome,
                       CASE WHEN $1::timestamptz-j.missing_since < interval '1 hour' THEN 0
                            WHEN $1::timestamptz-j.missing_since < interval '6 hours' THEN 1
                            WHEN $1::timestamptz-j.missing_since < interval '24 hours' THEN 2
                            WHEN $1::timestamptz-j.missing_since < interval '3 days' THEN 3
                            ELSE 4 END AS age
                FROM job_posting j JOIN company c ON c.id=j.company_id
                LEFT JOIN reconcile_queue q ON q.posting_id=j.id AND q.missing_since=j.missing_since
                WHERE j.status='active' AND j.missing_since IS NOT NULL
                  AND ($2::text IS NULL OR split_part(j.source,':',1)=$2)
                  AND ($3::text IS NULL OR coalesce(q.last_outcome,'unchecked')=$3)
                  AND ($4='' OR j.title ILIKE $5 OR c.name ILIKE $5 OR j.source ILIKE $5)
            )
            SELECT *,count(*) OVER() AS total FROM missing
            WHERE ($6::integer IS NULL OR age=$6)
            ORDER BY missing_since,id LIMIT $7 OFFSET $8
        """, snapshot["observed_at"], vendor, outcome, q, pattern, age, limit, offset)
    return {"total": rows[0]["total"] if rows else 0, "rows": [dict(r) for r in rows]}
