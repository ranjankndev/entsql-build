"""Sample rows in model/samples/<TABLE>.csv: read, validate, save, and fill with LLM help.

Every row, typed by hand or returned by an LLM, is validated here before it reaches disk:
known columns, column types and lengths, NOT NULL, primary key uniqueness and parent key existence.
CHECK constraints are left to the database and fail the rebuild.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from benchlib.coltypes import json_schema, normalize_value
from benchlib.config import Paths
from benchlib.llm.base import LLMProvider
from benchlib.model import Column, Model, Relation, Table

PARENT_VALUES_IN_PROMPT = 20
EXISTING_ROWS_IN_PROMPT = 50
KNOWN_VALUES_IN_MESSAGE = 10

SYSTEM_PROMPT = (
    "You write realistic, internally consistent sample rows for a text-to-SQL benchmark database. "
    "Follow the user's instruction exactly. Every value must fit its column type, length, NOT NULL and CHECK rules. "
    "A column that references another table must hold one of the listed existing key values, or NULL when the "
    "column allows it and the instruction asks for it. Never reuse a primary key of an existing sample row. "
    'Reply as JSON {"rows": [...]}, one object per row with every column present and null for NULL.'
)

Row = dict[str, str | None]


class SamplesError(Exception):
    """The request cannot be carried out (unknown table, bad file header, unusable LLM reply)."""


@dataclass
class RejectedRow:
    number: int
    row: Any
    problems: list[str]


@dataclass
class ValidationResult:
    accepted: list[Row]
    rejected: list[RejectedRow]


@dataclass
class FillResult:
    path: Path
    requested: int
    appended: list[Row]
    rejected: list[RejectedRow]
    extra_valid_rows: int = 0
    dropped_columns: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ files


def samples_path(paths: Paths, table_name: str) -> Path:
    return paths.samples / f"{table_name}.csv"


def read_csv_rows(path: Path) -> tuple[list[str], list[Row]]:
    """Header and rows of a CSV file; empty cells are None. A missing file has no header and no rows."""
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [{key: (value if value != "" else None) for key, value in row.items() if key is not None} for row in reader]
        return list(reader.fieldnames or []), rows


def column_values(path: Path, name: str) -> Iterator[str]:
    """Non-empty values of one column, streamed so large data files are fine."""
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if name not in header:
            return
        index = header.index(name)
        for row in reader:
            if index < len(row) and row[index] != "":
                yield row[index]


def file_columns(path: Path, table: Table, header: list[str]) -> list[Column]:
    """Columns stored in the file: its header, or every table column for a new file."""
    if not header:
        return list(table.columns)
    unknown = [name for name in header if table.column(name) is None]
    if unknown:
        raise SamplesError(f"{path}: header has columns that {table.name} does not have: {', '.join(unknown)}")
    return [table.column(name) for name in header]


def write_rows(path: Path, columns: list[Column], rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([column.name for column in columns])
        writer.writerows([[row.get(column.name) or "" for column in columns] for row in rows])


def append_rows(path: Path, columns: list[Column], rows: list[Row]) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        write_rows(path, columns, rows)
        return
    needs_newline = not path.read_bytes().endswith(b"\n")
    with path.open("a", newline="", encoding="utf-8") as handle:
        if needs_newline:
            handle.write("\n")
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows([[row.get(column.name) or "" for column in columns] for row in rows])


# ------------------------------------------------------------- validation


def require_table(model: Model, name: str) -> Table:
    table = model.table(name)
    if table is None:
        raise SamplesError(f"unknown table {name}; tables are: {', '.join(model.tables)}")
    return table


def outgoing_relations(model: Model, table: Table) -> list[Relation]:
    return [r for r in model.relations if r.from_table == table.name and r.to_table in model.tables]


def parent_values(paths: Paths, model: Model, relation: Relation) -> list[str]:
    """Existing parent key values from the parent's samples and generated data, normalized, in file order."""
    parent = model.tables[relation.to_table]
    column = parent.column(relation.to_column)
    found: dict[str, None] = {}
    if column is None:
        return []
    for path in (samples_path(paths, parent.name), paths.data / f"{parent.name}.csv"):
        for raw in column_values(path, column.name):
            value, problem = normalize_value(column, raw)
            if value is not None and problem is None:
                found.setdefault(value, None)
    return list(found)


def normalize_row(table: Table, columns: list[Column], raw: Any) -> tuple[Row, list[str]]:
    if not isinstance(raw, dict):
        return {}, [f"expected an object with column values, got {raw!r}"]
    problems = [f"unknown column {key}" for key in raw if table.column(str(key)) is None]
    row: Row = {}
    for column in columns:
        value, problem = normalize_value(column, raw.get(column.name))
        if problem is not None:
            problems.append(f"{column.name}: {problem}")
        row[column.name] = value
    return row, problems


def reference_problems(row: Row, parents: list[tuple[Relation, set[str]]]) -> list[str]:
    problems = []
    for relation, values in parents:
        value = row.get(relation.from_column)
        if value is not None and value not in values:
            known = ", ".join(sorted(values)[:KNOWN_VALUES_IN_MESSAGE]) or "none yet"
            problems.append(f"{relation.from_column}: {value!r} does not exist in {relation.to_ref} (existing values include: {known})")
    return problems


def validate_rows(
    table: Table,
    columns: list[Column],
    rows: list[Any],
    existing_rows: list[Row],
    parents: list[tuple[Relation, list[str]]],
) -> ValidationResult:
    """Check each row; primary keys must be new relative to existing_rows and to earlier accepted rows."""
    pk_names = [column.name for column in table.pk_columns]
    seen = {tuple(normalize_row(table, columns, row)[0].get(name) for name in pk_names) for row in existing_rows}
    parent_sets = [(relation, set(values)) for relation, values in parents]
    accepted: list[Row] = []
    rejected: list[RejectedRow] = []
    for number, raw in enumerate(rows, start=1):
        row, problems = normalize_row(table, columns, raw)
        if isinstance(raw, dict):
            problems += reference_problems(row, parent_sets)
            key = tuple(row.get(name) for name in pk_names)
            if pk_names and None not in key and key in seen:
                problems.append(f"primary key ({', '.join(str(v) for v in key)}) already exists in the sample rows")
        if problems:
            rejected.append(RejectedRow(number=number, row=raw, problems=problems))
        else:
            accepted.append(row)
            seen.add(tuple(row.get(name) for name in pk_names))
    return ValidationResult(accepted=accepted, rejected=rejected)


# ------------------------------------------------------------------- LLM


def response_schema(table: Table) -> dict[str, Any]:
    row = {
        "type": "object",
        "properties": {column.name: json_schema(column) for column in table.columns},
        "required": [column.name for column in table.columns],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": row}},
        "required": ["rows"],
        "additionalProperties": False,
    }


def describe_table(table: Table) -> str:
    lines = [f"Table {table.name}" + (f": {table.description}" if table.description else ""), "Columns:"]
    for column in table.columns:
        flags = ["primary key"] if column.pk else []
        if not column.nullable and not column.pk:
            flags.append("NOT NULL")
        if column.default is not None:
            flags.append(f"DEFAULT {column.default}")
        if column.check is not None:
            flags.append(f"CHECK ({column.check})")
        text = f"- {column.name} {column.type}" + (f" [{', '.join(flags)}]" if flags else "")
        lines.append(text + (f" -- {column.description}" if column.description else ""))
    return "\n".join(lines)


def build_user_prompt(
    table: Table,
    instruction: str,
    count: int,
    existing_rows: list[Row],
    parents: list[tuple[Relation, list[str]]],
) -> str:
    parts = [describe_table(table)]
    if parents:
        lines = []
        for relation, values in parents:
            shown = ", ".join(values[:PARENT_VALUES_IN_PROMPT]) or "none yet"
            note = f", {relation.note}" if relation.note else ""
            lines.append(f"- {relation.from_column} references {relation.to_ref} ({relation.kind}{note}); existing values: {shown}")
        parts.append("References:\n" + "\n".join(lines))
    if existing_rows:
        shown_rows = existing_rows[:EXISTING_ROWS_IN_PROMPT]
        parts.append(f"Existing sample rows ({len(shown_rows)} of {len(existing_rows)}):\n{json.dumps(shown_rows, ensure_ascii=False)}")
    parts.append(f"Instruction: {instruction}")
    parts.append(f"Return exactly {count} new rows.")
    return "\n\n".join(parts)


def fill_samples(paths: Paths, model: Model, table_name: str, instruction: str, count: int, provider: LLMProvider) -> FillResult:
    """Ask the provider for rows, validate them, append up to count valid rows to the samples file."""
    table = require_table(model, table_name)
    if count < 1:
        raise SamplesError("the number of rows must be at least 1")
    if not instruction.strip():
        raise SamplesError("the instruction is empty")
    path = samples_path(paths, table.name)
    header, existing = read_csv_rows(path)
    columns = file_columns(path, table, header)
    parents = [(relation, parent_values(paths, model, relation)) for relation in outgoing_relations(model, table)]
    user = build_user_prompt(table, instruction, count, existing, parents)
    reply = provider.complete_json(SYSTEM_PROMPT, user, response_schema(table))
    rows = reply.get("rows")
    if not isinstance(rows, list):
        raise SamplesError(f"the LLM reply has no rows list: {str(reply)[:200]}")
    result = validate_rows(table, columns, rows, existing, parents)
    appended = result.accepted[:count]
    if appended:
        append_rows(path, columns, appended)
    stored = {column.name for column in columns}
    dropped = [c.name for c in table.columns if c.name not in stored and any(isinstance(r, dict) and r.get(c.name) not in (None, "") for r in rows)]
    return FillResult(
        path=path,
        requested=count,
        appended=appended,
        rejected=result.rejected,
        extra_valid_rows=len(result.accepted) - len(appended),
        dropped_columns=dropped,
    )


# -------------------------------------------------------------- data page


def sample_frame(paths: Paths, table: Table) -> pd.DataFrame:
    """Samples as text for st.data_editor; a table without a file shows its columns and no rows."""
    path = samples_path(paths, table.name)
    header, rows = read_csv_rows(path)
    names = [column.name for column in file_columns(path, table, header)]
    return pd.DataFrame(rows, columns=names).astype("string")


def save_records(paths: Paths, model: Model, table_name: str, records: list[dict[str, Any]]) -> ValidationResult:
    """Validate every edited row; write the file only when all rows are valid. Blank rows are dropped."""
    table = require_table(model, table_name)
    path = samples_path(paths, table.name)
    header, _ = read_csv_rows(path)
    columns = file_columns(path, table, header or [str(key) for key in records[0]] if records else header)
    rows = [record for record in records if any(value not in (None, "") for value in record.values())]
    parents = [(relation, parent_values(paths, model, relation)) for relation in outgoing_relations(model, table)]
    result = validate_rows(table, columns, rows, [], parents)
    if not result.rejected:
        write_rows(path, columns, result.accepted)
    return result


def data_preview(paths: Paths, table: Table, limit: int = 50) -> pd.DataFrame:
    path = paths.data / f"{table.name}.csv"
    if not path.is_file():
        return pd.DataFrame(columns=[column.name for column in table.columns])
    return pd.read_csv(path, nrows=limit, dtype=str, keep_default_na=False)
