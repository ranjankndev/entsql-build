"""Schema model: load, validate and save model/mybank.yaml (PLAN section 3)."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MODEL_KEYS = ("schema", "version", "seed", "tables", "relations")
TABLE_KEYS = ("description", "columns", "rows", "generation")
COLUMN_KEYS = ("name", "type", "pk", "nullable", "default", "check", "description")
RELATION_KEYS = ("from", "to", "declared", "kind", "note")
GENERATORS = ("seq", "faker", "choice", "int", "decimal", "date", "ref", "copy", "expr", "const")
GENERATOR_OPTIONS = ("null_rate", "dist")
DISTRIBUTIONS = ("uniform", "zipf")


class ModelError(Exception):
    """The model cannot be read or is invalid; errors lists every problem found."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False
    nullable: bool = True
    default: str | None = None
    check: str | None = None
    description: str | None = None

    @property
    def required(self) -> bool:
        """True when the column may not hold NULL."""
        return self.pk or not self.nullable


@dataclass
class Table:
    name: str
    columns: list[Column]
    description: str | None = None
    rows: int = 0
    generation: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def pk_columns(self) -> list[Column]:
        return [column for column in self.columns if column.pk]

    def column(self, name: str) -> Column | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass
class Relation:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    declared: bool = False
    kind: str = "parent"
    note: str | None = None

    @property
    def from_ref(self) -> str:
        return f"{self.from_table}.{self.from_column}"

    @property
    def to_ref(self) -> str:
        return f"{self.to_table}.{self.to_column}"


@dataclass
class Model:
    schema: str
    version: int
    seed: int
    tables: dict[str, Table]
    relations: list[Relation] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return self.tables.get(name)


# ---------------------------------------------------------------- loading


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last one."""


def construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise ModelError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping)


def load_model(path: Path) -> Model:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ModelError(f"{path} not found") from exc
    return parse_model(text)


def parse_model(text: str) -> Model:
    try:
        data = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ModelError(f"invalid YAML: {exc}") from exc
    return model_from_dict(data)


def model_from_dict(data: Any) -> Model:
    """Build the dataclasses; structural problems (wrong shapes, unknown keys) raise ModelError."""
    if not isinstance(data, dict):
        raise ModelError("the model file must be a YAML mapping")
    errors = unknown_keys(data, MODEL_KEYS, "model")
    raw_tables = data.get("tables") or {}
    if not isinstance(raw_tables, dict):
        errors.append("tables must be a mapping of table name to definition")
        raw_tables = {}
    tables: dict[str, Table] = {}
    for name, raw in raw_tables.items():
        table = table_from_dict(str(name), raw, errors)
        if table is not None:
            tables[table.name] = table
    raw_relations = data.get("relations") or []
    if not isinstance(raw_relations, list):
        errors.append("relations must be a list")
        raw_relations = []
    relations = [r for r in (relation_from_dict(raw, errors) for raw in raw_relations) if r is not None]
    schema, version, seed = data.get("schema"), data.get("version", 0), data.get("seed")
    if not isinstance(schema, str):
        errors.append("schema must be a string")
    if not is_int(version):
        errors.append("version must be an integer")
    if not is_int(seed):
        errors.append("seed must be an integer")
    if errors:
        raise ModelError("invalid model file", errors)
    return Model(schema=schema, version=version, seed=seed, tables=tables, relations=relations)


def table_from_dict(name: str, raw: Any, errors: list[str]) -> Table | None:
    where = f"table {name}"
    if not isinstance(raw, dict):
        errors.append(f"{where}: must be a mapping")
        return None
    errors.extend(unknown_keys(raw, TABLE_KEYS, where))
    raw_columns = raw.get("columns") or []
    if not isinstance(raw_columns, list):
        errors.append(f"{where}: columns must be a list")
        raw_columns = []
    columns = [c for c in (column_from_dict(where, rc, errors) for rc in raw_columns) if c is not None]
    rows = raw.get("rows", 0)
    if not is_int(rows):
        errors.append(f"{where}: rows must be an integer")
        rows = 0
    generation = raw.get("generation") or {}
    if not isinstance(generation, dict):
        errors.append(f"{where}: generation must be a mapping of column to spec")
        generation = {}
    return Table(
        name=name,
        columns=columns,
        description=optional_text(raw.get("description")),
        rows=rows,
        generation={str(column): spec for column, spec in generation.items()},
    )


def column_from_dict(where: str, raw: Any, errors: list[str]) -> Column | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("type"), str):
        errors.append(f"{where}: every column needs a name and a type, got {raw!r}")
        return None
    errors.extend(unknown_keys(raw, COLUMN_KEYS, f"{where} column {raw['name']}"))
    pk = raw.get("pk", False)
    nullable = raw.get("nullable", not pk)
    if not isinstance(pk, bool) or not isinstance(nullable, bool):
        errors.append(f"{where} column {raw['name']}: pk and nullable must be true or false")
        return None
    return Column(
        name=raw["name"],
        type=raw["type"],
        pk=pk,
        nullable=nullable,
        default=optional_text(raw.get("default")),
        check=optional_text(raw.get("check")),
        description=optional_text(raw.get("description")),
    )


def relation_from_dict(raw: Any, errors: list[str]) -> Relation | None:
    if not isinstance(raw, dict):
        errors.append(f"relation must be a mapping, got {raw!r}")
        return None
    where = f"relation {raw.get('from')} -> {raw.get('to')}"
    errors.extend(unknown_keys(raw, RELATION_KEYS, where))
    source, target = split_ref(raw.get("from")), split_ref(raw.get("to"))
    declared = raw.get("declared", False)
    if source is None or target is None:
        errors.append(f"{where}: from and to must be TABLE.COLUMN")
        return None
    if not isinstance(declared, bool):
        errors.append(f"{where}: declared must be true or false")
        return None
    return Relation(
        from_table=source[0],
        from_column=source[1],
        to_table=target[0],
        to_column=target[1],
        declared=declared,
        kind=str(raw.get("kind", "parent")),
        note=optional_text(raw.get("note")),
    )


def unknown_keys(raw: dict, allowed: tuple[str, ...], where: str) -> list[str]:
    return [f"{where}: unknown key {key!r}" for key in raw if key not in allowed]


def split_ref(text: Any) -> tuple[str, str] | None:
    """'TABLE.COLUMN' -> ('TABLE', 'COLUMN'); None when the text has another shape."""
    if not isinstance(text, str):
        return None
    parts = [part.strip() for part in text.split(".")]
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


# ------------------------------------------------------------- validation


def validate_model(model: Model) -> list[str]:
    """Every semantic problem in the model; an empty list means valid."""
    errors: list[str] = []
    if not IDENTIFIER.match(model.schema):
        errors.append(f"schema {model.schema!r} is not a valid identifier")
    if model.version < 0:
        errors.append("version must not be negative")
    if not model.tables:
        errors.append("model has no tables")
    errors.extend(duplicate_names(list(model.tables), "table"))
    for table in model.tables.values():
        errors.extend(validate_table(table))
        errors.extend(validate_generation(model, table))
    errors.extend(validate_relations(model))
    try:
        generation_order(model)
    except ModelError as exc:
        errors.append(str(exc))
    return errors


def duplicate_names(names: list[str], what: str) -> list[str]:
    """Names are compared case-insensitively because PostgreSQL folds unquoted names to lower case."""
    seen: set[str] = set()
    errors = []
    for name in names:
        if name.lower() in seen:
            errors.append(f"duplicate {what} {name} (names are case-insensitive)")
        seen.add(name.lower())
    return errors


def validate_table(table: Table) -> list[str]:
    where = f"table {table.name}"
    errors = []
    if not IDENTIFIER.match(table.name):
        errors.append(f"{where}: name is not a valid identifier")
    if not table.columns:
        errors.append(f"{where}: has no columns")
    errors.extend(f"{where}: {error}" for error in duplicate_names([c.name for c in table.columns], "column"))
    for column in table.columns:
        if not IDENTIFIER.match(column.name):
            errors.append(f"{where}: column name {column.name!r} is not a valid identifier")
        if not column.type.strip():
            errors.append(f"{where}: column {column.name} has no type")
        if column.pk and column.nullable:
            errors.append(f"{where}: primary key column {column.name} cannot be nullable")
    if table.rows < 0:
        errors.append(f"{where}: rows must not be negative")
    return errors


def validate_generation(model: Model, table: Table) -> list[str]:
    errors = []
    for column_name, spec in table.generation.items():
        where = f"{table.name}.{column_name} generation"
        column = table.column(column_name)
        if column is None:
            errors.append(f"{where}: unknown column")
            continue
        errors.extend(f"{where}: {error}" for error in spec_errors(model, table, column, spec))
    return errors


def spec_errors(model: Model, table: Table, column: Column, spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return ["must be a mapping such as {seq: 1}"]
    errors = [f"unknown key {key!r}" for key in spec if key not in GENERATORS and key not in GENERATOR_OPTIONS]
    kinds = [key for key in spec if key in GENERATORS]
    if len(kinds) != 1:
        return errors + [f"needs exactly one of {', '.join(GENERATORS)}; found {', '.join(kinds) or 'none'}"]
    errors.extend(generator_errors(model, table, kinds[0], spec[kinds[0]]))
    if "dist" in spec and (kinds[0] != "ref" or spec["dist"] not in DISTRIBUTIONS):
        errors.append("dist is only allowed with ref and must be uniform or zipf")
    if "null_rate" in spec:
        rate = spec["null_rate"]
        if not is_number(rate) or not 0 <= rate <= 1:
            errors.append("null_rate must be a number between 0 and 1")
        elif rate > 0 and column.required:
            errors.append("null_rate on a NOT NULL or primary key column")
    return errors


def generator_errors(model: Model, table: Table, kind: str, arg: Any) -> list[str]:
    if kind == "seq":
        return [] if is_int(arg) else ["seq needs an integer start value"]
    if kind == "faker":
        ok = isinstance(arg, str) and arg != "" and not arg.startswith("_")
        return [] if ok else ["faker needs a provider name such as company"]
    if kind == "choice":
        return choice_errors(arg)
    if kind == "int":
        ok = isinstance(arg, list) and len(arg) == 2 and all(is_int(v) for v in arg) and arg[0] <= arg[1]
        return [] if ok else ["int needs [lo, hi] integers with lo <= hi"]
    if kind == "decimal":
        ok = (
            isinstance(arg, list) and len(arg) == 3 and is_number(arg[0]) and is_number(arg[1])
            and is_int(arg[2]) and arg[2] >= 0 and arg[0] <= arg[1]
        )
        return [] if ok else ["decimal needs [lo, hi, scale] with lo <= hi and scale >= 0"]
    if kind == "date":
        dates = [parse_date(v) for v in arg] if isinstance(arg, list) and len(arg) == 2 else [None]
        ok = None not in dates and dates[0] <= dates[1]
        return [] if ok else ["date needs [from, to] as YYYY-MM-DD with from <= to"]
    if kind == "ref":
        return ref_errors(model, arg, "ref")
    if kind == "copy":
        return copy_errors(model, table, arg)
    if kind == "expr":
        return expr_errors(arg)
    return []  # const accepts any value


def choice_errors(arg: Any) -> list[str]:
    if not isinstance(arg, dict) or not arg:
        return ["choice needs a mapping of value to weight"]
    weights = list(arg.values())
    if not all(is_number(w) and w >= 0 for w in weights) or sum(weights) <= 0:
        return ["choice weights must be non-negative numbers with a positive sum"]
    return []


def ref_errors(model: Model, text: Any, what: str) -> list[str]:
    parts = split_ref(text)
    if parts is None:
        return [f"{what} needs TABLE.COLUMN, got {text!r}"]
    table = model.table(parts[0])
    if table is None:
        return [f"{what}: unknown table {parts[0]}"]
    if table.column(parts[1]) is None:
        return [f"{what}: unknown column {text}"]
    return []


def copy_errors(model: Model, table: Table, arg: Any) -> list[str]:
    if not isinstance(arg, dict) or set(arg) != {"from", "via"}:
        return ["copy needs {from: TABLE.COLUMN, via: LOCAL_COLUMN}"]
    errors = ref_errors(model, arg["from"], "copy from")
    via = str(arg["via"])
    if table.column(via) is None:
        errors.append(f"copy via: unknown column {via}")
    if not errors and copy_relation(model, table.name, via, split_ref(arg["from"])[0]) is None:
        errors.append(f"copy via {via}: no relation from {table.name}.{via} to {split_ref(arg['from'])[0]}")
    return errors


def copy_relation(model: Model, table_name: str, via: str, parent: str) -> Relation | None:
    """The relation that lets a child column copy a value from its parent row."""
    for relation in model.relations:
        if relation.from_table == table_name and relation.from_column == via and relation.to_table == parent:
            return relation
    return None


def expr_errors(arg: Any) -> list[str]:
    if not isinstance(arg, str):
        return ["expr needs a Python expression string"]
    try:
        compile(arg, "<expr>", "eval")
    except SyntaxError as exc:
        return [f"expr does not compile: {exc.msg}"]
    return []


def validate_relations(model: Model) -> list[str]:
    errors = []
    seen: set[tuple[str, str]] = set()
    for relation in model.relations:
        where = f"relation {relation.from_ref} -> {relation.to_ref}"
        for table_name, column_name in ((relation.from_table, relation.from_column), (relation.to_table, relation.to_column)):
            table = model.table(table_name)
            if table is None:
                errors.append(f"{where}: unknown table {table_name}")
            elif table.column(column_name) is None:
                errors.append(f"{where}: unknown column {table_name}.{column_name}")
        if (relation.from_ref, relation.to_ref) in seen:
            errors.append(f"{where}: duplicate relation")
        seen.add((relation.from_ref, relation.to_ref))
        target = model.table(relation.to_table)
        if relation.declared and target is not None and target.column(relation.to_column) is not None:
            if [c.name for c in target.pk_columns] != [relation.to_column]:
                errors.append(f"{where}: a declared relation must point at the single-column primary key of {relation.to_table}")
    return errors


def generation_order(model: Model) -> list[str]:
    """Tables with parents before children over all relations; ties keep YAML order."""
    parents = {
        name: {r.to_table for r in model.relations if r.from_table == name and r.to_table != name and r.to_table in model.tables}
        for name in model.tables
    }
    order: list[str] = []
    while len(order) < len(parents):
        ready = [name for name, needed in parents.items() if name not in order and needed <= set(order)]
        if not ready:
            stuck = [name for name in parents if name not in order]
            raise ModelError(f"relations form a cycle between: {', '.join(stuck)}")
        order.extend(ready)
    return order


# ----------------------------------------------------------------- saving


class FlowMap(dict):
    """A mapping written on one line in YAML flow style, like {name: ID, type: integer}."""


class ModelDumper(yaml.SafeDumper):
    """Indents block lists under their key and writes FlowMap values in flow style."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def represent_flow_map(dumper: ModelDumper, data: FlowMap) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


def represent_text(dumper: ModelDumper, data: str) -> yaml.Node:
    """SQL literals read better double-quoted: "'A'" rather than '''A'''."""
    style = '"' if "'" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


ModelDumper.add_representer(FlowMap, represent_flow_map)
ModelDumper.add_representer(str, represent_text)


def column_to_dict(column: Column) -> FlowMap:
    data = FlowMap(name=column.name, type=column.type)
    if column.pk:
        data["pk"] = True
    if column.nullable != (not column.pk):
        data["nullable"] = column.nullable
    for key in ("default", "check", "description"):
        if getattr(column, key) is not None:
            data[key] = getattr(column, key)
    return data


def table_to_dict(table: Table) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if table.description is not None:
        data["description"] = table.description
    data["columns"] = [column_to_dict(column) for column in table.columns]
    data["rows"] = table.rows
    if table.generation:
        data["generation"] = {column: FlowMap(spec) for column, spec in table.generation.items()}
    return data


def relation_to_dict(relation: Relation) -> FlowMap:
    data = FlowMap({"from": relation.from_ref, "to": relation.to_ref, "declared": relation.declared, "kind": relation.kind})
    if relation.note is not None:
        data["note"] = relation.note
    return data


def model_to_dict(model: Model) -> dict[str, Any]:
    return {
        "schema": model.schema,
        "version": model.version,
        "seed": model.seed,
        "tables": {name: table_to_dict(table) for name, table in model.tables.items()},
        "relations": [relation_to_dict(relation) for relation in model.relations],
    }


def dump_model(model: Model) -> str:
    return yaml.dump(model_to_dict(model), Dumper=ModelDumper, sort_keys=False, allow_unicode=True, width=1000)


def save_model(model: Model, path: Path) -> None:
    """Write the YAML atomically so a crash never leaves a half-written model."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(dump_model(model), encoding="utf-8")
    os.replace(temporary, path)
