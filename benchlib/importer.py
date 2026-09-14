"""Bootstrap the model YAML from a PostgreSQL DDL file, parsed with pglast."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pglast import ast, parse_sql
from pglast.enums import AlterTableType, ConstrType, ObjectType
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor

from benchlib.model import Column, Model, ModelError, Relation, Table, save_model, validate_model

DEFAULT_SCHEMA = "mybank"
DEFAULT_SEED = 1
NAME_PART = rb'"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*'
QUALIFIED_NAME = re.compile(rb"(?:" + NAME_PART + rb")(?:\s*\.\s*(?:" + NAME_PART + rb"))*")
SINGLE_NAME = re.compile(NAME_PART)
QUIET_STATEMENTS = (ast.CreateSchemaStmt, ast.VariableSetStmt, ast.TransactionStmt)


@dataclass
class ForeignKey:
    table: str
    column: str
    parent: str
    parent_column: str | None


@dataclass
class ImportState:
    """Everything collected while walking one DDL file."""

    source: bytes
    tables: dict[str, Table] = field(default_factory=dict)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: str | None = None


@dataclass
class ImportResult:
    model: Model
    warnings: list[str]
    errors: list[str]  # validation errors of the imported model


class ColumnRefCollector(Visitor):
    """Collects the column names referenced by an expression."""

    def __init__(self) -> None:
        super().__init__()
        self.names: set[str] = set()

    def visit_ColumnRef(self, ancestors: object, node: ast.ColumnRef) -> None:
        last = node.fields[-1]
        if isinstance(last, ast.String):
            self.names.add(last.sval)


def import_file(sql_path: Path, model_path: Path, force: bool = False) -> ImportResult:
    """Parse sql_path and write model_path; refuses to overwrite an existing model unless force."""
    if model_path.exists() and not force:
        raise ModelError(f"{model_path} already exists; import is a one-time bootstrap (use --force to overwrite)")
    try:
        sql = sql_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ModelError(f"{sql_path} not found") from exc
    result = import_ddl(sql)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    save_model(result.model, model_path)
    return result


def import_ddl(sql: str, schema: str | None = None, seed: int = DEFAULT_SEED) -> ImportResult:
    try:
        statements = parse_sql(sql)
    except ParseError as exc:
        raise ModelError(f"cannot parse DDL: {exc}") from exc
    state = ImportState(source=sql.encode("utf-8"))
    for raw in statements:
        import_statement(state, raw)
    relations = resolve_foreign_keys(state)
    model = Model(
        schema=schema or state.schema or DEFAULT_SCHEMA,
        version=0,
        seed=seed,
        tables=state.tables,
        relations=relations,
    )
    return ImportResult(model=model, warnings=state.warnings, errors=validate_model(model))


def import_statement(state: ImportState, raw: ast.RawStmt) -> None:
    node = raw.stmt
    if isinstance(node, ast.CreateStmt):
        import_create_table(state, node)
    elif isinstance(node, ast.AlterTableStmt):
        import_alter_table(state, node)
    elif isinstance(node, ast.CommentStmt):
        import_comment(state, node)
    elif not isinstance(node, QUIET_STATEMENTS):
        text = state.source[raw.stmt_location : raw.stmt_location + raw.stmt_len].decode("utf-8", "replace")
        state.warnings.append(f"skipped statement: {first_line(text)}")


def import_create_table(state: ImportState, node: ast.CreateStmt) -> None:
    name = source_name(state, node.relation.location, node.relation.relname)
    if node.relation.schemaname and state.schema is None:
        state.schema = node.relation.schemaname
    table = Table(name=name, columns=[])
    state.tables[name] = table
    for element in node.tableElts or ():
        if isinstance(element, ast.ColumnDef):
            import_column(state, table, element)
        elif isinstance(element, ast.Constraint):
            import_constraint(state, table, element)
        else:
            state.warnings.append(f"{name}: skipped {type(element).__name__}")


def import_column(state: ImportState, table: Table, node: ast.ColumnDef) -> None:
    column = Column(name=source_name(state, node.location, node.colname), type=type_text(node.typeName))
    table.columns.append(column)
    for constraint in node.constraints or ():
        kind = ConstrType(constraint.contype)
        if kind == ConstrType.CONSTR_NOTNULL:
            column.nullable = False
        elif kind == ConstrType.CONSTR_DEFAULT:
            column.default = deparse(constraint.raw_expr)
        elif kind == ConstrType.CONSTR_CHECK:
            column.check = combine_checks(column.check, deparse(constraint.raw_expr))
        elif kind == ConstrType.CONSTR_PRIMARY:
            column.pk, column.nullable = True, False
        elif kind == ConstrType.CONSTR_FOREIGN:
            add_foreign_key(state, table.name, [column.name], constraint)
        elif kind != ConstrType.CONSTR_NULL:
            state.warnings.append(f"{table.name}.{column.name}: skipped {kind.name} constraint")


def import_constraint(state: ImportState, table: Table, node: ast.Constraint) -> None:
    """Table-level constraint, from CREATE TABLE or ALTER TABLE ... ADD CONSTRAINT."""
    kind = ConstrType(node.contype)
    if kind == ConstrType.CONSTR_PRIMARY:
        set_primary_key(state, table, string_values(node.keys))
    elif kind == ConstrType.CONSTR_FOREIGN:
        add_foreign_key(state, table.name, string_values(node.fk_attrs), node)
    elif kind == ConstrType.CONSTR_CHECK:
        attach_table_check(state, table, node)
    else:
        state.warnings.append(f"{table.name}: skipped {kind.name} constraint")


def set_primary_key(state: ImportState, table: Table, keys: list[str]) -> None:
    columns = [find_column(table, key) for key in keys]
    if None in columns:
        state.warnings.append(f"{table.name}: skipped primary key on unknown column(s) {', '.join(keys)}")
        return
    for column in columns:
        column.pk, column.nullable = True, False
    if [c.name for c in columns] != [c.name for c in table.pk_columns]:
        state.warnings.append(f"{table.name}: primary key order differs from column order; the model uses column order")


def add_foreign_key(state: ImportState, table_name: str, columns: list[str], node: ast.Constraint) -> None:
    parent_columns = string_values(node.pk_attrs)
    if len(columns) != 1 or len(parent_columns) > 1:
        state.warnings.append(f"{table_name}: skipped multi-column foreign key ({', '.join(columns)})")
        return
    parent = source_name(state, node.pktable.location, node.pktable.relname)
    state.foreign_keys.append(ForeignKey(table_name, columns[0], parent, parent_columns[0] if parent_columns else None))


def attach_table_check(state: ImportState, table: Table, node: ast.Constraint) -> None:
    expression = deparse(node.raw_expr)
    collector = ColumnRefCollector()
    collector(node.raw_expr)
    column = find_column(table, next(iter(collector.names))) if len(collector.names) == 1 else None
    if column is None:
        state.warnings.append(f"{table.name}: skipped CHECK ({expression}); the model holds single-column checks only")
        return
    column.check = combine_checks(column.check, expression)


def import_alter_table(state: ImportState, node: ast.AlterTableStmt) -> None:
    name = source_name(state, node.relation.location, node.relation.relname)
    table = find_table(state.tables, name)
    if table is None:
        state.warnings.append(f"ALTER TABLE {name}: table is not created earlier in the file, skipped")
        return
    for command in node.cmds or ():
        subtype = AlterTableType(command.subtype)
        if subtype == AlterTableType.AT_AddConstraint and isinstance(command.def_, ast.Constraint):
            import_constraint(state, table, command.def_)
        else:
            state.warnings.append(f"ALTER TABLE {table.name}: skipped {subtype.name}")


def import_comment(state: ImportState, node: ast.CommentStmt) -> None:
    parts = [getattr(item, "sval", "") for item in node.object] if isinstance(node.object, tuple) else []
    kind = ObjectType(node.objtype)
    if kind == ObjectType.OBJECT_TABLE and parts:
        table = find_table(state.tables, parts[-1])
        if table is not None:
            table.description = node.comment
            return
    if kind == ObjectType.OBJECT_COLUMN and len(parts) >= 2:
        table = find_table(state.tables, parts[-2])
        column = find_column(table, parts[-1]) if table is not None else None
        if column is not None:
            column.description = node.comment
            return
    state.warnings.append(f"skipped COMMENT ON {kind.name.removeprefix('OBJECT_')} {'.'.join(parts)}")


def resolve_foreign_keys(state: ImportState) -> list[Relation]:
    """Turn collected foreign keys into declared relations, using the tables' own spelling of names."""
    relations = []
    for fk in state.foreign_keys:
        child = find_table(state.tables, fk.table)
        parent = find_table(state.tables, fk.parent)
        column = find_column(child, fk.column) if child is not None else None
        target = None
        if parent is not None and fk.parent_column is not None:
            target = find_column(parent, fk.parent_column)
        elif parent is not None and len(parent.pk_columns) == 1:
            target = parent.pk_columns[0]
        if child is None or column is None or target is None:
            state.warnings.append(f"skipped foreign key {fk.table}.{fk.column} -> {fk.parent}: table or column not found")
            continue
        relations.append(Relation(child.name, column.name, parent.name, target.name, declared=True, kind="parent"))
    return relations


def source_name(state: ImportState, location: int | None, parsed: str) -> str:
    """The name as spelled in the DDL (pglast lower-cases unquoted names); the last part if qualified."""
    if location is None or location < 0:
        return parsed
    match = QUALIFIED_NAME.match(state.source, location)
    if match is None:
        return parsed
    last = SINGLE_NAME.findall(match.group(0))[-1].decode("utf-8")
    name = last[1:-1].replace('""', '"') if last.startswith('"') else last
    return name if name.lower() == parsed.lower() else parsed


def type_text(type_name: ast.TypeName) -> str:
    """Type as SQL text; char(n) keeps its length, which pglast omits for char(1)."""
    base = type_name.names[-1].sval if type_name.names else ""
    if base == "bpchar" and type_name.typmods and not type_name.arrayBounds:
        return f"char({deparse(type_name.typmods[0])})"
    return deparse(type_name)


def deparse(node: ast.Node) -> str:
    return RawStream()(node)


def combine_checks(existing: str | None, new: str) -> str:
    return new if existing is None else f"({existing}) AND ({new})"


def string_values(nodes: tuple | None) -> list[str]:
    return [node.sval for node in nodes or ()]


def find_table(tables: dict[str, Table], name: str) -> Table | None:
    return next((table for table in tables.values() if table.name.lower() == name.lower()), None)


def find_column(table: Table, name: str) -> Column | None:
    return next((column for column in table.columns if column.name.lower() == name.lower()), None)


def first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= 80 else line[:77] + "..."
