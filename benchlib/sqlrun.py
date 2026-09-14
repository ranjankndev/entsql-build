"""Ad-hoc SQL as the read-only role, with the model schema on the search path (bench sql, SQL page)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from benchlib.config import DbProfile, Paths
from benchlib.db import connect_read, execute
from benchlib.dialects import get_dialect
from benchlib.model import load_model


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    elapsed_seconds: float
    truncated: bool


def run_sql(paths: Paths, profile: DbProfile, query: str, max_rows: int = 10_000, verbose: bool = False) -> QueryResult:
    """Run one query in a read-only transaction that is always rolled back."""
    schema = load_model(paths.model).schema
    dialect = get_dialect(profile.dialect)
    conn = connect_read(profile)
    try:
        execute(conn, dialect.search_path_sql(schema, local=True), verbose=verbose)
        started = time.perf_counter()
        cursor = execute(conn, query, verbose=verbose)
        rows = cursor.fetchmany(max_rows + 1) if cursor.description else []
        elapsed = time.perf_counter() - started
        columns = [column.name for column in cursor.description or []]
        return QueryResult(columns=columns, rows=rows[:max_rows], elapsed_seconds=elapsed, truncated=len(rows) > max_rows)
    finally:
        conn.rollback()
        conn.close()


def format_value(value: Any) -> str:
    return "NULL" if value is None else str(value).replace("\n", "\\n")


def format_table(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    """Plain aligned text table, NULL shown as NULL."""
    cells = [[format_value(value) for value in row] for row in rows]
    widths = [max([len(name), *(len(row[i]) for row in cells)]) for i, name in enumerate(columns)]
    lines = [" | ".join(name.ljust(width) for name, width in zip(columns, widths))]
    lines.append("-+-".join("-" * width for width in widths))
    lines += [" | ".join(value.ljust(width) for value, width in zip(row, widths)) for row in cells]
    return "\n".join(line.rstrip() for line in lines)


def result_summary(result: QueryResult) -> str:
    count = len(result.rows)
    more = ", truncated" if result.truncated else ""
    return f"({count} row{'' if count == 1 else 's'}{more}, {result.elapsed_seconds:.3f} s)"
