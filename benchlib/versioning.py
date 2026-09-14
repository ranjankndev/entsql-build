"""model commit: bump the version, snapshot YAML and DDL into versions/, append the changelog, git commit."""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from benchlib.build import render
from benchlib.config import Paths
from benchlib.dialects.base import Dialect
from benchlib.model import Model, ModelError, load_model, save_model, validate_model

CHANGELOG_HEADER = "# Model changelog\n\n"


@dataclass
class ModelDiff:
    added_tables: list[str] = field(default_factory=list)
    removed_tables: list[str] = field(default_factory=list)
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)


@dataclass
class CommitResult:
    version: int
    files: list[Path]
    changelog_line: str


def diff_models(old: Model | None, new: Model) -> ModelDiff:
    old_tables = old.tables if old is not None else {}
    diff = ModelDiff(
        added_tables=[name for name in new.tables if name not in old_tables],
        removed_tables=[name for name in old_tables if name not in new.tables],
    )
    for name, table in new.tables.items():
        if name not in old_tables:
            continue
        old_names = {column.name for column in old_tables[name].columns}
        new_names = {column.name for column in table.columns}
        diff.added_columns += [f"{name}.{c.name}" for c in table.columns if c.name not in old_names]
        diff.removed_columns += [f"{name}.{c.name}" for c in old_tables[name].columns if c.name not in new_names]
    return diff


def changelog_line(version: int, message: str, diff: ModelDiff, day: dt.date) -> str:
    parts = [f"- v{version:03d} ({day.isoformat()}) {message}"]
    sections = (
        ("added tables", diff.added_tables),
        ("removed tables", diff.removed_tables),
        ("added columns", diff.added_columns),
        ("removed columns", diff.removed_columns),
    )
    parts += [f"{label}: {', '.join(items)}" for label, items in sections if items]
    if len(parts) == 1:
        parts.append("no table or column changes")
    return "; ".join(parts)


def snapshot_path(paths: Paths, version: int, suffix: str) -> Path:
    return paths.versions / f"{paths.model.stem}_v{version:03d}{suffix}"


def latest_snapshot(paths: Paths) -> Path | None:
    pattern = re.compile(rf"^{re.escape(paths.model.stem)}_v(\d+)\.yaml$")
    found = [(int(match.group(1)), path) for path in paths.versions.glob("*.yaml") if (match := pattern.match(path.name))]
    return max(found)[1] if found else None


def commit_version(paths: Paths, dialect: Dialect, message: str, today: dt.date, git: bool = True) -> CommitResult:
    model = load_model(paths.model)
    errors = validate_model(model)
    if errors:
        raise ModelError("model is invalid", errors)
    version = model.version + 1
    yaml_snapshot, sql_snapshot = snapshot_path(paths, version, ".yaml"), snapshot_path(paths, version, ".sql")
    existing = [str(path) for path in (yaml_snapshot, sql_snapshot) if path.exists()]
    if existing:
        raise ModelError(f"snapshot already exists: {', '.join(existing)}")
    previous_path = latest_snapshot(paths)
    previous = load_model(previous_path) if previous_path is not None else None

    model.version = version
    save_model(model, paths.model)
    render(model, paths, dialect)
    paths.versions.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths.model, yaml_snapshot)
    shutil.copyfile(paths.ddl, sql_snapshot)
    line = changelog_line(version, message, diff_models(previous, model), today)
    changelog = paths.versions / "CHANGELOG.md"
    append_changelog(changelog, line)

    files = [paths.model, paths.ddl, paths.diagram, yaml_snapshot, sql_snapshot, changelog]
    if git:
        git_commit(paths.root, files, f"model v{version:03d}: {message}")
    return CommitResult(version=version, files=files, changelog_line=line)


def append_changelog(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else CHANGELOG_HEADER
    path.write_text(text + line + "\n", encoding="utf-8")


def git_commit(root: Path, files: list[Path], message: str) -> None:
    """Commit exactly these files, leaving anything else staged untouched."""
    relative = [str(path.relative_to(root)) for path in files]
    subprocess.run(["git", "add", "--", *relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message, "--", *relative], cwd=root, check=True)
