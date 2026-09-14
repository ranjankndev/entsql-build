"""Deterministic data generation from the generation specs in the model (PLAN section 3).

Every column draws from its own random.Random seeded with "<seed>/<table>/<column>/<purpose>", so
the same YAML always produces byte-identical CSVs, and adding a column or a table leaves the values
of the others unchanged. There is no wall-clock time and no unseeded randomness in this module.

Order inside a table: seq, faker, choice, int, decimal, date, ref and const columns first, then copy
columns, then expr columns row by row. An expr is a Python expression that sees:
  row       dict of the values generated so far for this row
  i         0-based row number
  random    the column's own seeded random.Random
  date, datetime, timedelta, Decimal, and the builtins listed in EXPR_BUILTINS.
null_rate turns that share of a column's values into NULL, with its own seeded stream.
Reference values come from the parent's generated data plus its sample rows.
"""

from __future__ import annotations

import builtins
import csv
import datetime as dt
import itertools
import os
import random
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from faker import Faker

from benchlib.config import Paths
from benchlib.model import GENERATORS, Model, ModelError, Table, copy_relation, generation_order, parse_date, split_ref, validate_model

Columns = dict[str, list[Any]]
INDEPENDENT_GENERATORS = ("seq", "faker", "choice", "int", "decimal", "date", "ref", "const")
EXPR_BUILTINS = {name: getattr(builtins, name) for name in ("abs", "bool", "float", "int", "len", "max", "min", "round", "str", "sum")}
EXPR_NAMES = {"date": dt.date, "datetime": dt.datetime, "timedelta": dt.timedelta, "Decimal": Decimal}
ZIPF_EXPONENT = 1.0


class GenError(Exception):
    """A spec cannot be generated: missing parent data, unknown faker provider, failing expr, ..."""


@dataclass
class GenResult:
    rows: dict[str, int] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    removed: list[Path] = field(default_factory=list)


def generate(paths: Paths, model: Model, tables: list[str] | None = None) -> GenResult:
    """Write data/<TABLE>.csv for the given tables (all when None), parents before children."""
    errors = validate_model(model)
    if errors:
        raise ModelError("model is invalid", errors)
    unknown = [name for name in tables or [] if name not in model.tables]
    if unknown:
        raise GenError(f"unknown table(s): {', '.join(unknown)}")
    selected = [name for name in generation_order(model) if not tables or name in tables]
    generated: dict[str, Columns] = {}
    parent_cache: dict[str, Columns] = {}
    result = GenResult()
    paths.data.mkdir(parents=True, exist_ok=True)
    for name in selected:
        table = model.tables[name]
        path = paths.data / f"{name}.csv"
        generated[name] = generate_table(paths, model, table, generated, parent_cache)
        result.rows[name] = table.rows
        if table.rows == 0:
            if path.exists():
                path.unlink()
                result.removed.append(path)
            continue
        write_csv(path, generated[name])
        result.files[name] = path
    return result


def generate_table(paths: Paths, model: Model, table: Table, generated: dict[str, Columns], parent_cache: dict[str, Columns]) -> Columns:
    count = table.rows
    check_table_specs(table)
    specs = table.generation
    ordered = [column.name for column in table.columns if column.name in specs]
    kinds = {name: generator_kind(specs[name]) for name in ordered}
    columns: Columns = {}
    for name in ordered:
        if kinds[name] in INDEPENDENT_GENERATORS:
            values = independent_values(paths, model, table, name, kinds[name], count, generated, parent_cache)
            columns[name] = apply_null_rate(model.seed, table.name, name, specs[name], values)
    for name in ordered:
        if kinds[name] == "copy":
            values = copy_values(paths, model, table, name, columns, generated, parent_cache)
            columns[name] = apply_null_rate(model.seed, table.name, name, specs[name], values)
    expr_names = [name for name in ordered if kinds[name] == "expr"]
    if expr_names:
        columns.update(expr_values(model.seed, table, expr_names, columns, count))
    return {name: columns[name] for name in ordered}


def check_table_specs(table: Table) -> None:
    if table.rows == 0:
        return
    if not table.generation:
        raise GenError(f"{table.name}: rows is {table.rows} but there is no generation spec")
    for column in table.columns:
        if column.name not in table.generation and column.required and column.default is None:
            raise GenError(f"{table.name}.{column.name} is NOT NULL without a default, so it needs a generation spec")


def generator_kind(spec: dict[str, Any]) -> str:
    return next(key for key in spec if key in GENERATORS)


def column_rng(seed: int, table: str, column: str, purpose: str = "values") -> random.Random:
    return random.Random(f"{seed}/{table}/{column}/{purpose}")


# ------------------------------------------------------------- generators


def independent_values(
    paths: Paths,
    model: Model,
    table: Table,
    column: str,
    kind: str,
    count: int,
    generated: dict[str, Columns],
    parent_cache: dict[str, Columns],
) -> list[Any]:
    spec = table.generation[column]
    arg = spec[kind]
    rng = column_rng(model.seed, table.name, column)
    if kind == "seq":
        return list(range(arg, arg + count))
    if kind == "const":
        return [arg] * count
    if kind == "choice":
        return rng.choices(list(arg), weights=list(arg.values()), k=count)
    if kind == "int":
        return [rng.randint(arg[0], arg[1]) for _ in range(count)]
    if kind == "decimal":
        return decimal_values(rng, arg, count)
    if kind == "date":
        return date_values(rng, arg, count)
    if kind == "faker":
        return faker_values(model.seed, table.name, column, arg, count)
    values = parent_key_values(paths, model, arg, generated, parent_cache)
    return ref_values(rng, values, spec.get("dist", "uniform"), count, f"{table.name}.{column}")


def decimal_values(rng: random.Random, arg: list[Any], count: int) -> list[Decimal]:
    """Uniform over [lo, hi] in steps of 10^-scale, drawn as integers so no float rounding is involved."""
    low, high, scale = arg
    lowest = int(Decimal(str(low)).scaleb(scale).to_integral_value(rounding=ROUND_CEILING))
    highest = int(Decimal(str(high)).scaleb(scale).to_integral_value(rounding=ROUND_FLOOR))
    return [Decimal(rng.randint(lowest, highest)).scaleb(-scale) for _ in range(count)]


def date_values(rng: random.Random, arg: list[Any], count: int) -> list[dt.date]:
    first, last = (parse_date(value).toordinal() for value in arg)
    return [dt.date.fromordinal(rng.randint(first, last)) for _ in range(count)]


def faker_values(seed: int, table: str, column: str, provider: str, count: int) -> list[Any]:
    fake = Faker()
    fake.seed_instance(f"{seed}/{table}/{column}/faker")
    method = getattr(fake, provider, None)
    if provider.startswith("_") or not callable(method):
        raise GenError(f"{table}.{column}: unknown faker provider {provider!r}")
    return [method() for _ in range(count)]


def ref_values(rng: random.Random, values: list[Any], dist: str, count: int, where: str) -> list[Any]:
    if count == 0:
        return []
    if not values:
        raise GenError(f"{where}: the referenced table has no rows; give it rows and a spec, or sample rows")
    if dist == "zipf":
        weights = itertools.accumulate(1.0 / rank**ZIPF_EXPONENT for rank in range(1, len(values) + 1))
        return rng.choices(values, cum_weights=list(weights), k=count)
    return rng.choices(values, k=count)


def copy_values(
    paths: Paths,
    model: Model,
    table: Table,
    column: str,
    columns: Columns,
    generated: dict[str, Columns],
    parent_cache: dict[str, Columns],
) -> list[Any]:
    """The parent row's value, found through the local via column; sample rows win over generated rows."""
    arg = table.generation[column]["copy"]
    parent_name, parent_column = split_ref(arg["from"])
    via = str(arg["via"])
    if via not in columns:
        raise GenError(f"{table.name}.{column}: copy via {via} needs {via} to have a non-expr spec")
    relation = copy_relation(model, table.name, via, parent_name)
    parent = parent_columns(paths, model, parent_name, generated, parent_cache)
    mapping = {str(key): value for key, value in zip(parent[relation.to_column], parent[parent_column])}
    return [None if key is None else mapping.get(str(key)) for key in columns[via]]


def expr_values(seed: int, table: Table, names: list[str], columns: Columns, count: int) -> Columns:
    codes = {name: compile(table.generation[name]["expr"], f"<{table.name}.{name} expr>", "eval") for name in names}
    namespaces = {name: {"__builtins__": EXPR_BUILTINS, **EXPR_NAMES, "random": column_rng(seed, table.name, name)} for name in names}
    rates = {name: table.generation[name].get("null_rate", 0) for name in names}
    null_rngs = {name: column_rng(seed, table.name, name, "null") for name in names}
    results: Columns = {name: [] for name in names}
    base = list(columns)
    for i in range(count):
        row = {name: columns[name][i] for name in base}
        for name in names:
            namespace = namespaces[name]
            namespace["row"], namespace["i"] = row, i
            try:
                value = eval(codes[name], namespace)
            except Exception as exc:
                raise GenError(f"{table.name}.{name}: expr failed on row {i}: {exc!r}") from exc
            if rates[name] and null_rngs[name].random() < rates[name]:
                value = None
            row[name] = value
            results[name].append(value)
    return results


def apply_null_rate(seed: int, table: str, column: str, spec: dict[str, Any], values: list[Any]) -> list[Any]:
    rate = spec.get("null_rate", 0)
    if not rate:
        return values
    rng = column_rng(seed, table, column, "null")
    return [None if rng.random() < rate else value for value in values]


# ------------------------------------------------------------ parent data


def parent_columns(paths: Paths, model: Model, name: str, generated: dict[str, Columns], parent_cache: dict[str, Columns]) -> Columns:
    """All columns of a parent table: generated rows (this run or data/<T>.csv) followed by its sample rows."""
    if name in parent_cache:
        return parent_cache[name]
    table = model.tables[name]
    data_path = paths.data / f"{name}.csv"
    if name in generated:
        data = generated[name]
    elif data_path.is_file():
        data = read_csv_columns(data_path)
    elif table.rows > 0:
        raise GenError(f"data/{name}.csv is missing; generate {name} first")
    else:
        data = {}
    sample_rows = read_csv_columns(paths.samples / f"{name}.csv")
    data_count, sample_count = column_length(data), column_length(sample_rows)
    merged = {
        column.name: list(data.get(column.name, [None] * data_count)) + sample_rows.get(column.name, [None] * sample_count)
        for column in table.columns
    }
    parent_cache[name] = merged
    return merged


def parent_key_values(paths: Paths, model: Model, ref: str, generated: dict[str, Columns], parent_cache: dict[str, Columns]) -> list[Any]:
    """Distinct non-NULL values of the referenced column, in first-seen order."""
    parent_name, column = split_ref(ref)
    distinct: dict[str, Any] = {}
    for value in parent_columns(paths, model, parent_name, generated, parent_cache)[column]:
        if value is not None:
            distinct.setdefault(str(value), value)
    return list(distinct.values())


def read_csv_columns(path: Path) -> Columns:
    """A CSV file as column lists of strings, empty cells as None; a missing file is empty."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        columns: Columns = {name: [] for name in header}
        for row in reader:
            for name, value in zip(header, row):
                columns[name].append(value if value != "" else None)
    return columns


def column_length(columns: Columns) -> int:
    return len(next(iter(columns.values()), []))


# ----------------------------------------------------------------- output


def csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def write_csv(path: Path, columns: Columns) -> None:
    """Write atomically; NULL is an empty cell, which COPY ... (FORMAT csv) reads as NULL."""
    temporary = path.with_name(path.name + ".tmp")
    names = list(columns)
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(names)
        writer.writerows(zip(*(map(csv_cell, columns[name]) for name in names)))
    os.replace(temporary, path)


def data_row_counts(paths: Paths, model: Model) -> dict[str, int | None]:
    """Rows in each data/<TABLE>.csv, None when the file does not exist."""
    counts: dict[str, int | None] = {}
    for name in model.tables:
        path = paths.data / f"{name}.csv"
        if not path.is_file():
            counts[name] = None
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            counts[name] = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
    return counts
