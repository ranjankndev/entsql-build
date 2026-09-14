"""Command line entry point. Parses arguments and calls benchlib; no logic lives here."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

if sys.version_info < (3, 12):
    sys.exit("bench needs Python 3.12 or newer")

from benchlib import build, importer, versioning
from benchlib.config import Config, ConfigError, load_config
from benchlib.dialects import get_dialect
from benchlib.model import ModelError, load_model

# Commands that are still stubs, with the PLAN step that implements them.
PLANNED_STEP = {
    "rebuild": "P2",
    "sql": "P2",
    "samples fill": "P4",
    "gen": "P5",
    "check": "P6",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="Text-to-SQL benchmark workbench")
    parser.add_argument("--db", help="database profile from bench.toml (default: $BENCH_DB or local)")
    parser.add_argument("--llm", help="LLM profile from bench.toml (default: $BENCH_LLM or none)")
    parser.add_argument("--verbose", action="store_true", help="print every SQL statement")
    commands = parser.add_subparsers(dest="command", required=True)

    model = commands.add_parser("model", help="import, render and version the schema model")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    model_import = model_commands.add_parser("import", help="parse a DDL file into model/mybank.yaml")
    model_import.add_argument("file")
    model_import.add_argument("--force", action="store_true", help="overwrite an existing model YAML")
    model_commands.add_parser("render", help="validate the YAML, write build/mybank.sql and .svg")
    model_commit = model_commands.add_parser("commit", help="render, bump version, snapshot, git commit")
    model_commit.add_argument("-m", "--message", required=True)

    rebuild = commands.add_parser("rebuild", help="regenerate data and rebuild the schema in one transaction")
    rebuild.add_argument("--no-generate", action="store_true", help="skip data generation")

    commands.add_parser("status", help="compare current files with the last build")

    samples = commands.add_parser("samples", help="hand-crafted sample rows")
    samples_commands = samples.add_subparsers(dest="samples_command", required=True)
    fill = samples_commands.add_parser("fill", help="ask the LLM for sample rows")
    fill.add_argument("table")
    fill.add_argument("instruction")
    fill.add_argument("-n", type=int, default=5, help="number of rows (default 5)")

    gen = commands.add_parser("gen", help="generate data/<TABLE>.csv")
    gen.add_argument("tables", nargs="*", metavar="TABLE")

    commands.add_parser("check", help="run structural checks against the database")

    sql = commands.add_parser("sql", help="run a query as the read-only role")
    sql.add_argument("query")
    return parser


def command_key(args: argparse.Namespace) -> str:
    subcommand = getattr(args, f"{args.command}_command", None)
    return f"{args.command} {subcommand}" if subcommand else args.command


def display(config: Config, path: Path) -> str:
    try:
        return str(path.relative_to(config.paths.root))
    except ValueError:
        return str(path)


def run_status(config: Config, args: argparse.Namespace) -> int:
    print(build.build_status(config.paths))
    return 0


def run_model_import(config: Config, args: argparse.Namespace) -> int:
    result = importer.import_file(Path(args.file), config.paths.model, force=args.force)
    for warning in result.warnings:
        print(f"warning: {warning}")
    model = result.model
    print(f"wrote {display(config, config.paths.model)}: {len(model.tables)} tables, {len(model.relations)} relations")
    for error in result.errors:
        print(f"error: {error}")
    return 1 if result.errors else 0


def run_model_render(config: Config, args: argparse.Namespace) -> int:
    model = load_model(config.paths.model)
    result = build.render(model, config.paths, get_dialect(config.db.dialect))
    print(f"wrote {display(config, result.ddl)}")
    print(f"wrote {display(config, result.diagram)}")
    return 0


def run_model_commit(config: Config, args: argparse.Namespace) -> int:
    dialect = get_dialect(config.db.dialect)
    result = versioning.commit_version(config.paths, dialect, args.message, dt.date.today())
    print(result.changelog_line)
    print(f"committed model v{result.version:03d}: {', '.join(display(config, f) for f in result.files)}")
    return 0


HANDLERS: dict[str, Callable[[Config, argparse.Namespace], int]] = {
    "status": run_status,
    "model import": run_model_import,
    "model render": run_model_render,
    "model commit": run_model_commit,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(db=args.db, llm=args.llm)
    except ConfigError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    key = command_key(args)
    handler = HANDLERS.get(key)
    if handler is None:
        print(f"bench {key}: not implemented yet (step {PLANNED_STEP[key]})", file=sys.stderr)
        return 1
    try:
        return handler(config, args)
    except ModelError as exc:
        print(f"bench {key}: {exc}", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"bench {key}: command failed: {' '.join(exc.cmd)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
