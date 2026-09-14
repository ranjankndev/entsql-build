"""Command line entry point. Parses arguments and calls benchlib; no logic lives here."""

from __future__ import annotations

import argparse
import sys

if sys.version_info < (3, 12):
    sys.exit("bench needs Python 3.12 or newer")

from benchlib import build
from benchlib.config import Config, ConfigError, load_config

# Commands that are still stubs, with the PLAN step that implements them.
PLANNED_STEP = {
    "model import": "P1",
    "model render": "P1",
    "model commit": "P1",
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


def run_status(config: Config) -> int:
    print(build.build_status(config.paths))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(db=args.db, llm=args.llm)
    except ConfigError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    key = command_key(args)
    if key == "status":
        return run_status(config)
    print(f"bench {key}: not implemented yet (step {PLANNED_STEP[key]})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
