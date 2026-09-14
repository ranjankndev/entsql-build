"""Model editing for the UI: tables, columns, generation specs and relations as editable rows.

Every function returns a new Model and never changes the one it is given.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
import yaml

from benchlib.config import Paths
from benchlib.model import (
    IDENTIFIER,
    Column,
    Model,
    ModelError,
    Relation,
    Table,
    UniqueKeyLoader,
    save_model,
    split_ref,
    validate_model,
)

COLUMN_FIELDS = ["name", "type", "pk", "nullable", "default", "check", "description"]
GENERATION_FIELDS = ["column", "spec"]
RELATION_FIELDS = ["from", "to", "declared", "kind", "note"]
BOOL_FIELDS = {"pk", "nullable", "declared"}


@dataclass
class TableEdit:
    """The table form as the user left it."""

    name: str
    description: str | None
    rows: int
    columns: list[dict[str, Any]]
    generation: list[dict[str, Any]]


# ------------------------------------------------------------ cell values


def clean(value: Any) -> Any:
    """Empty editor cells arrive as None, NaN, <NA> or ''; all become None. Text is stripped."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def as_bool(value: Any, default: bool) -> bool:
    value = clean(value)
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "y")
    return bool(value)


def as_text(value: Any) -> str | None:
    value = clean(value)
    return None if value is None else str(value)


def frame(records: list[dict[str, Any]], fields: list[str]) -> pd.DataFrame:
    """DataFrame for st.data_editor: bool fields as bool, everything else as nullable strings."""
    data = pd.DataFrame(records, columns=fields)
    return data.astype({field: (bool if field in BOOL_FIELDS else "string") for field in fields})


def frame_records(data: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows of an edited DataFrame as dicts with empty cells as None."""
    plain = data.astype(object).where(data.notna(), None)
    return [{key: clean(value) for key, value in row.items()} for row in plain.to_dict("records")]


# -------------------------------------------------------------- columns


def column_records(table: Table) -> list[dict[str, Any]]:
    return [
        {
            "name": column.name,
            "type": column.type,
            "pk": column.pk,
            "nullable": column.nullable,
            "default": column.default,
            "check": column.check,
            "description": column.description,
        }
        for column in table.columns
    ]


def column_frame(table: Table) -> pd.DataFrame:
    return frame(column_records(table), COLUMN_FIELDS)


def columns_from_records(table_name: str, records: list[dict[str, Any]]) -> tuple[list[Column], list[str]]:
    """Blank rows are skipped; a primary key column is always NOT NULL."""
    columns, errors = [], []
    for number, raw in enumerate(records, start=1):
        name, type_ = as_text(raw.get("name")), as_text(raw.get("type"))
        if name is None and type_ is None:
            continue
        if name is None or type_ is None:
            errors.append(f"{table_name} column row {number}: name and type are both required")
            continue
        pk = as_bool(raw.get("pk"), False)
        columns.append(
            Column(
                name=name,
                type=type_,
                pk=pk,
                nullable=False if pk else as_bool(raw.get("nullable"), True),
                default=as_text(raw.get("default")),
                check=as_text(raw.get("check")),
                description=as_text(raw.get("description")),
            )
        )
    return columns, errors


# ----------------------------------------------------------- generation


def spec_text(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(spec, default_flow_style=True, width=1000, sort_keys=False).strip()


def generation_records(table: Table) -> list[dict[str, Any]]:
    return [{"column": column, "spec": spec_text(spec)} for column, spec in table.generation.items()]


def generation_frame(table: Table) -> pd.DataFrame:
    return frame(generation_records(table), GENERATION_FIELDS)


def generation_from_records(table_name: str, records: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    generation: dict[str, dict[str, Any]] = {}
    errors = []
    for number, raw in enumerate(records, start=1):
        column, text = as_text(raw.get("column")), as_text(raw.get("spec"))
        if column is None and text is None:
            continue
        where = f"{table_name} generation row {number}"
        if column is None or text is None:
            errors.append(f"{where}: column and spec are both required")
            continue
        try:
            spec = yaml.load(text, Loader=UniqueKeyLoader)
        except (yaml.YAMLError, ModelError) as exc:
            errors.append(f"{where}: spec is not valid YAML ({exc})")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{where}: spec must be a mapping such as {{seq: 1}}")
        elif column in generation:
            errors.append(f"{where}: {column} has more than one spec")
        else:
            generation[column] = spec
    return generation, errors


# ------------------------------------------------------------ relations


def relation_records(model: Model) -> list[dict[str, Any]]:
    return [
        {"from": r.from_ref, "to": r.to_ref, "declared": r.declared, "kind": r.kind, "note": r.note}
        for r in model.relations
    ]


def relation_frame(model: Model) -> pd.DataFrame:
    return frame(relation_records(model), RELATION_FIELDS)


def relations_from_records(records: list[dict[str, Any]]) -> tuple[list[Relation], list[str]]:
    relations, errors = [], []
    for number, raw in enumerate(records, start=1):
        source, target = as_text(raw.get("from")), as_text(raw.get("to"))
        if source is None and target is None:
            continue
        source_ref, target_ref = split_ref(source), split_ref(target)
        if source_ref is None or target_ref is None:
            errors.append(f"relation row {number}: from and to must be TABLE.COLUMN")
            continue
        relations.append(
            Relation(
                from_table=source_ref[0],
                from_column=source_ref[1],
                to_table=target_ref[0],
                to_column=target_ref[1],
                declared=as_bool(raw.get("declared"), False),
                kind=as_text(raw.get("kind")) or "parent",
                note=as_text(raw.get("note")),
            )
        )
    return relations, errors


# ---------------------------------------------------------------- tables


def add_table(model: Model, name: str) -> tuple[Model, list[str]]:
    """New table with a single integer primary key column <NAME>_ID."""
    name = (name or "").strip()
    if not IDENTIFIER.match(name):
        return model, [f"table name {name!r} is not a valid identifier"]
    if any(existing.lower() == name.lower() for existing in model.tables):
        return model, [f"table {name} already exists"]
    updated = copy.deepcopy(model)
    updated.tables[name] = Table(name=name, columns=[Column(name=f"{name}_ID", type="integer", pk=True, nullable=False)])
    return updated, []


def delete_table(model: Model, name: str) -> Model:
    """Removes the table and every relation that touches it."""
    updated = copy.deepcopy(model)
    updated.tables.pop(name, None)
    updated.relations = [r for r in updated.relations if name not in (r.from_table, r.to_table)]
    return updated


def rename_table(model: Model, old: str, new: str) -> tuple[Model, list[str]]:
    """Renames the table and follows the rename in relations and ref/copy generation specs."""
    if new == old:
        return model, []
    if not IDENTIFIER.match(new):
        return model, [f"table name {new!r} is not a valid identifier"]
    if any(name.lower() == new.lower() for name in model.tables if name != old):
        return model, [f"table {new} already exists"]
    updated = copy.deepcopy(model)
    updated.tables = {(new if name == old else name): table for name, table in updated.tables.items()}
    updated.tables[new].name = new
    for relation in updated.relations:
        relation.from_table = new if relation.from_table == old else relation.from_table
        relation.to_table = new if relation.to_table == old else relation.to_table
    for table in updated.tables.values():
        table.generation = {column: rename_in_spec(spec, old, new) for column, spec in table.generation.items()}
    return updated, []


def rename_ref(text: Any, old: str, new: str) -> Any:
    parts = split_ref(text)
    return f"{new}.{parts[1]}" if parts is not None and parts[0] == old else text


def rename_in_spec(spec: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    updated = dict(spec)
    if "ref" in updated:
        updated["ref"] = rename_ref(updated["ref"], old, new)
    if isinstance(updated.get("copy"), dict):
        updated["copy"] = {**updated["copy"], "from": rename_ref(updated["copy"].get("from"), old, new)}
    return updated


def apply_edits(model: Model, table_name: str, edit: TableEdit, relation_rows: list[dict[str, Any]]) -> tuple[Model, list[str]]:
    """Apply the table form and the relation editor. On any error the original model comes back unchanged."""
    columns, errors = columns_from_records(table_name, edit.columns)
    generation, generation_errors = generation_from_records(table_name, edit.generation)
    relations, relation_errors = relations_from_records(relation_rows)
    errors += generation_errors + relation_errors
    if edit.rows < 0:
        errors.append(f"{table_name}: rows must not be negative")
    if errors:
        return model, errors
    updated = copy.deepcopy(model)
    table = updated.tables[table_name]
    table.columns, table.generation = columns, generation
    table.description, table.rows = as_text(edit.description), int(edit.rows)
    updated.relations = relations
    return rename_table(updated, table_name, (edit.name or "").strip())


def save_checked(model: Model, paths: Paths) -> list[str]:
    """Validate and write the YAML; nothing is written when the model is invalid."""
    errors = validate_model(model)
    if not errors:
        save_model(model, paths.model)
    return errors
