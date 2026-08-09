"""Audit a demo database for unfilled data.

"Every data entry filled with a sample" is checkable rather than a matter of
opinion, so this module states it as two rules:

1. every table carries at least one row, and
2. no column is NULL in every row of its table.

Anything failing either rule is reported by name.  ``EXPECTED_EMPTY`` lists the
columns that are *correctly* NULL everywhere because a value there would assert
something false — a merge that was never reversed has no ``reversed_at``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db import Database

__all__ = ["VerifyReport", "verify", "EXPECTED_EMPTY", "SKIP_TABLES"]


SKIP_TABLES = frozenset({"sqlite_sequence", "schema_migrations"})

# Columns whose NULL is the honest value for this corpus. Each entry has to
# name a reason; "we could not be bothered" is not one.
EXPECTED_EMPTY: dict[str, dict[str, str]] = {
    "job_entities": {
        # A surviving entity is not merged into anything.
        "merged_into_id": "only the absorbed side of a merge carries this",
    },
}


@dataclass
class VerifyReport:
    empty_tables: list[str] = field(default_factory=list)
    null_columns: list[tuple[str, str]] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    accepted: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.empty_tables and not self.null_columns

    def render(self) -> str:
        lines: list[str] = []
        total = sum(self.row_counts.values())
        lines.append(f"{len(self.row_counts)} tables, {total} rows")
        if self.empty_tables:
            lines.append("")
            lines.append(f"EMPTY TABLES ({len(self.empty_tables)}):")
            lines.extend(f"  {name}" for name in self.empty_tables)
        if self.null_columns:
            lines.append("")
            lines.append(f"ALL-NULL COLUMNS ({len(self.null_columns)}):")
            lines.extend(f"  {table}.{column}" for table, column in self.null_columns)
        if self.accepted:
            lines.append("")
            lines.append("accepted as legitimately empty:")
            lines.extend(
                f"  {table}.{column} — {reason}" for table, column, reason in self.accepted
            )
        lines.append("")
        lines.append("OK: every table populated, every column carries a sample" if self.ok
                     else "INCOMPLETE: see above")
        return "\n".join(lines)


async def verify(database: Database) -> VerifyReport:
    await database.initialize()
    report = VerifyReport()
    tables = await database.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    for row in tables:
        table = str(row["name"])
        if table in SKIP_TABLES or table.startswith("sqlite_"):
            continue
        count_row = await database.fetch_one(f'SELECT COUNT(*) AS total FROM "{table}"')
        total = int(count_row["total"]) if count_row else 0
        report.row_counts[table] = total
        if total == 0:
            report.empty_tables.append(table)
            continue
        columns = await database.fetch_all(f'PRAGMA table_info("{table}")')
        if not columns:
            continue
        selects = ", ".join(
            f'COUNT("{column["name"]}") AS "{column["name"]}"' for column in columns
        )
        filled = await database.fetch_one(f'SELECT {selects} FROM "{table}"')
        for column in columns:
            name = str(column["name"])
            if filled is not None and int(filled[name]) > 0:
                continue
            reason = EXPECTED_EMPTY.get(table, {}).get(name)
            if reason:
                report.accepted.append((table, name, reason))
            else:
                report.null_columns.append((table, name))
    return report
