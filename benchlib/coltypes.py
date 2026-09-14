"""Column types read from the model's PostgreSQL type strings: JSON schema and value checks for sample rows."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from benchlib.model import Column

INTEGER_TYPES = {
    "smallint": "smallint", "int2": "smallint", "smallserial": "smallint",
    "integer": "integer", "int": "integer", "int4": "integer", "serial": "integer",
    "bigint": "bigint", "int8": "bigint", "bigserial": "bigint",
}
INTEGER_LIMITS = {"smallint": 2**15, "integer": 2**31, "bigint": 2**63}
FLOAT_TYPES = {"real", "float4", "double precision", "float8", "float"}
TEXT_WITH_LENGTH = re.compile(r"^(char|character|bpchar|varchar|character varying)\s*(?:\(\s*(\d+)\s*\))?$")
NUMERIC = re.compile(r"^(numeric|decimal)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?$")
JSON_TYPES = {"integer": "integer", "decimal": "number", "float": "number", "boolean": "boolean"}
TRUE_WORDS, FALSE_WORDS = {"true", "t", "yes", "y", "1"}, {"false", "f", "no", "n", "0"}


@dataclass(frozen=True)
class ColumnType:
    kind: str  # integer, decimal, float, boolean, date, timestamp, char, varchar, text
    name: str
    length: int | None = None
    precision: int | None = None
    scale: int | None = None


def parse_type(text: str) -> ColumnType:
    """Unknown types (time, json, arrays, ...) are treated as free text."""
    name = " ".join(text.lower().split())
    if name.endswith("]"):
        return ColumnType("text", name)
    if name in INTEGER_TYPES:
        return ColumnType("integer", INTEGER_TYPES[name])
    if name in FLOAT_TYPES:
        return ColumnType("float", name)
    if name in ("boolean", "bool"):
        return ColumnType("boolean", name)
    if name == "date":
        return ColumnType("date", name)
    if name.startswith("timestamp"):
        return ColumnType("timestamp", name)
    if match := NUMERIC.match(name):
        precision = int(match.group(2)) if match.group(2) else None
        scale = int(match.group(3)) if match.group(3) else (0 if precision is not None else None)
        return ColumnType("decimal", match.group(1), precision=precision, scale=scale)
    if match := TEXT_WITH_LENGTH.match(name):
        kind = "varchar" if match.group(1) in ("varchar", "character varying") else "char"
        length = int(match.group(2)) if match.group(2) else (1 if kind == "char" else None)
        return ColumnType(kind, match.group(1), length=length)
    return ColumnType("text", name)


def json_schema(column: Column) -> dict[str, Any]:
    """JSON schema for one column value, as sent to the LLM."""
    column_type = parse_type(column.type)
    base = JSON_TYPES.get(column_type.kind, "string")
    notes = [column.type]
    if column_type.kind == "date":
        notes.append("YYYY-MM-DD")
    if column.check is not None:
        notes.append(f"CHECK ({column.check})")
    if column.description is not None:
        notes.append(column.description)
    return {"type": base if column.required else [base, "null"], "description": "; ".join(notes)}


def normalize_value(column: Column, value: Any) -> tuple[str | None, str | None]:
    """(CSV text, None) for a valid value, (None, problem) otherwise. None and '' mean NULL."""
    if value is None or value == "":
        return None, ("must not be NULL" if column.required else None)
    column_type = parse_type(column.type)
    try:
        return NORMALIZERS[column_type.kind](column_type, value), None
    except (ValueError, InvalidOperation, TypeError) as exc:
        return None, str(exc)


def normalize_integer(column_type: ColumnType, value: Any) -> str:
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"expected an integer, got {value!r}")
    try:
        number = int(value) if isinstance(value, (int, float)) else int(str(value).strip())
    except ValueError:
        raise ValueError(f"expected an integer, got {value!r}") from None
    limit = INTEGER_LIMITS[column_type.name]
    if not -limit <= number < limit:
        raise ValueError(f"{number} is out of range for {column_type.name}")
    return str(number)


def normalize_decimal(column_type: ColumnType, value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"expected a number, got {value!r}") from None
    if not number.is_finite():
        raise ValueError(f"expected a finite number, got {value!r}")
    if column_type.scale is not None:
        quantum = Decimal(1).scaleb(-column_type.scale)
        if number != number.quantize(quantum):
            raise ValueError(f"{value} has more than {column_type.scale} decimal places")
        number = number.quantize(quantum)
    if column_type.precision is not None:
        integer_digits = len(str(abs(number).to_integral_value(rounding=ROUND_DOWN)).lstrip("0"))
        if integer_digits > column_type.precision - (column_type.scale or 0):
            raise ValueError(f"{value} does not fit {column_type.name}({column_type.precision},{column_type.scale})")
    return format(number, "f")


def normalize_float(column_type: ColumnType, value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    try:
        return str(float(value))
    except ValueError:
        raise ValueError(f"expected a number, got {value!r}") from None


def normalize_boolean(column_type: ColumnType, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    word = str(value).strip().lower()
    if word in TRUE_WORDS or word in FALSE_WORDS:
        return "true" if word in TRUE_WORDS else "false"
    raise ValueError(f"expected true or false, got {value!r}")


def normalize_date(column_type: ColumnType, value: Any) -> str:
    try:
        return dt.date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"expected a date as YYYY-MM-DD, got {value!r}") from None


def normalize_timestamp(column_type: ColumnType, value: Any) -> str:
    try:
        return dt.datetime.fromisoformat(str(value).strip()).isoformat(sep=" ")
    except ValueError:
        raise ValueError(f"expected a timestamp as YYYY-MM-DD HH:MM:SS, got {value!r}") from None


def normalize_text(column_type: ColumnType, value: Any) -> str:
    if isinstance(value, (dict, list, bool)):
        raise ValueError(f"expected text, got {value!r}")
    text = str(value)
    if column_type.kind == "char":
        text = text.rstrip(" ")
    if column_type.length is not None and len(text) > column_type.length:
        raise ValueError(f"{text!r} is longer than {column_type.length} characters")
    return text


NORMALIZERS: dict[str, Callable[[ColumnType, Any], str]] = {
    "integer": normalize_integer,
    "decimal": normalize_decimal,
    "float": normalize_float,
    "boolean": normalize_boolean,
    "date": normalize_date,
    "timestamp": normalize_timestamp,
    "char": normalize_text,
    "varchar": normalize_text,
    "text": normalize_text,
}
