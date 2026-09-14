"""PostgreSQL dialect: DDL emission and CSV loading. The only module that writes PostgreSQL DDL or COPY."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Any

from pglast.keywords import RESERVED_KEYWORDS, TYPE_FUNC_NAME_KEYWORDS

from benchlib.db import execute
from benchlib.model import Column, Relation, Table

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KEYWORDS_NEEDING_QUOTES = set(RESERVED_KEYWORDS) | set(TYPE_FUNC_NAME_KEYWORDS)
COPY_OPTIONS = "WITH (FORMAT csv, HEADER true)"


class PostgresDialect:
    name = "postgres"

    def quote(self, ident: str) -> str:
        """Plain names stay unquoted, so PostgreSQL folds them to lower case; keywords are quoted in lower case to match."""
        if SIMPLE_IDENTIFIER.match(ident):
            lower = ident.lower()
            return f'"{lower}"' if lower in KEYWORDS_NEEDING_QUOTES else ident
        return '"' + ident.replace('"', '""') + '"'

    def qualified(self, schema: str, name: str) -> str:
        return f"{self.quote(schema)}.{self.quote(name)}"

    def literal(self, text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    def column_list(self, names: list[str]) -> str:
        return ", ".join(self.quote(name) for name in names)

    def create_schema_sql(self, schema: str) -> str:
        return f"CREATE SCHEMA {self.quote(schema)};"

    def drop_schema_sql(self, schema: str) -> str:
        return f"DROP SCHEMA IF EXISTS {self.quote(schema)} CASCADE;"

    def type_map(self, pg_type: str) -> str:
        return pg_type

    def table_ddl(self, table: Table, relations: list[Relation]) -> str:
        """CREATE TABLE with primary key, NOT NULL, DEFAULT and CHECK. PostgreSQL adds foreign keys with fk_ddl."""
        width = max((len(self.quote(column.name)) for column in table.columns), default=0)
        lines = [self.column_ddl(column, width) for column in table.columns]
        if table.pk_columns:
            lines.append(f"PRIMARY KEY ({self.column_list([c.name for c in table.pk_columns])})")
        body = ",\n    ".join(lines)
        return f"CREATE TABLE {self.quote(table.name)} (\n    {body}\n);"

    def column_ddl(self, column: Column, width: int) -> str:
        parts = [self.quote(column.name).ljust(width), self.type_map(column.type)]
        if not column.nullable and not column.pk:
            parts.append("NOT NULL")
        if column.default is not None:
            parts.append(f"DEFAULT {column.default}")
        if column.check is not None:
            parts.append(f"CHECK ({column.check})")
        return " ".join(parts)

    def fk_ddl(self, relation: Relation) -> str:
        return (
            f"ALTER TABLE {self.quote(relation.from_table)} ADD FOREIGN KEY ({self.quote(relation.from_column)}) "
            f"REFERENCES {self.quote(relation.to_table)} ({self.quote(relation.to_column)});"
        )

    def comment_sql(self, table: Table) -> str:
        lines = []
        if table.description is not None:
            lines.append(f"COMMENT ON TABLE {self.quote(table.name)} IS {self.literal(table.description)};")
        for column in table.columns:
            if column.description is not None:
                target = f"{self.quote(table.name)}.{self.quote(column.name)}"
                lines.append(f"COMMENT ON COLUMN {target} IS {self.literal(column.description)};")
        return "\n".join(lines)

    def load_csv(self, conn: Any, schema: str, table: Table, path: Path, verbose: bool = False) -> int:
        """COPY a CSV with a header row into the table; columns missing from the header get their defaults."""
        columns = csv_columns(path, table)
        sql = f"COPY {self.qualified(schema, table.name)} ({self.column_list(columns)}) FROM STDIN {COPY_OPTIONS}"
        return copy_file(conn, sql, path, verbose)

    def upsert_csv(self, conn: Any, schema: str, table: Table, path: Path, verbose: bool = False) -> int:
        """Insert or update CSV rows on the primary key, through a temporary staging table."""
        columns = csv_columns(path, table)
        pk = [column.name for column in table.pk_columns]
        missing = [name for name in pk if name not in columns]
        if not pk or missing:
            raise ValueError(f"{path}: upsert needs every primary key column of {table.name} in the header")
        target = self.qualified(schema, table.name)
        staging = self.quote(f"bench_upsert_{table.name}".lower())
        names = self.column_list(columns)
        updates = [name for name in columns if name not in pk]
        action = "DO UPDATE SET " + ", ".join(f"{self.quote(n)} = EXCLUDED.{self.quote(n)}" for n in updates) if updates else "DO NOTHING"
        execute(conn, f"CREATE TEMP TABLE {staging} ON COMMIT DROP AS SELECT {names} FROM {target} WITH NO DATA;", verbose=verbose)
        copy_file(conn, f"COPY {staging} ({names}) FROM STDIN {COPY_OPTIONS}", path, verbose)
        insert = f"INSERT INTO {target} ({names}) SELECT {names} FROM {staging} ON CONFLICT ({self.column_list(pk)}) {action};"
        count = execute(conn, insert, verbose=verbose).rowcount
        execute(conn, f"DROP TABLE {staging};", verbose=verbose)
        return count


def csv_columns(path: Path, table: Table) -> list[str]:
    """Header of a CSV file, checked against the table's columns."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), None)
    if not header:
        raise ValueError(f"{path}: empty file, expected a header row")
    unknown = [name for name in header if table.column(name) is None]
    if unknown:
        raise ValueError(f"{path}: unknown column(s) for {table.name}: {', '.join(unknown)}")
    return header


def copy_file(conn: Any, sql: str, path: Path, verbose: bool) -> int:
    if verbose:
        print(f"{sql};  -- from {path}", file=sys.stderr)
    with conn.cursor() as cursor:
        with cursor.copy(sql) as copy:
            with path.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    copy.write(chunk)
        return cursor.rowcount
