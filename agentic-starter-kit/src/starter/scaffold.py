"""`starter init` — turn the kit into *your* project.

Copying a template and then hand-editing 40 files is how a starter kit rots:
half the references get renamed, the other half quietly still say `starter`.
This does the rename mechanically and tells you exactly what it touched.

What it rewrites, and nothing else:

* `src/starter/` → `src/<package>/`, and every `import starter` /
  `from starter...` / `starter.module` reference in Python.
* the console script, package name and coverage/test paths in `pyproject.toml`.
* `starter <command>` invocations and `src/starter` paths in the Makefile,
  Dockerfile, CI workflows and docs.

What it deliberately leaves alone: English prose. "This starter kit" is a
sentence, not an identifier, and a scaffolder that mangles documentation to
look thorough is worse than one that admits its scope. The summary says which
files still mention the word so you can skim them.
"""

from __future__ import annotations

import keyword
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Derived, not hardcoded: a project scaffolded from the kit can itself scaffold
# another one, because it knows its own package name.
SOURCE_PACKAGE = __name__.split(".")[0]

# Never copied into a new project: caches, secrets, local state, VCS history.
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".memory",
    ".eval-runs",
    "dist",
    "build",
    "node_modules",
    ".egg-info",
}
EXCLUDE_FILES = {".env", ".DS_Store"}
TEXT_SUFFIXES = {
    ".py", ".toml", ".yaml", ".yml", ".md", ".json", ".jsonl", ".cfg", ".ini",
    ".txt", ".sh", ".bicep", ".example", "",
}

# Ordered: the most specific pattern must win before the bare-word one.
def _rules(package: str, source: str = SOURCE_PACKAGE) -> list[tuple[re.Pattern[str], str]]:
    src = re.escape(source)
    return [
        (re.compile(rf"\bsrc/{src}\b"), f"src/{package}"),
        (re.compile(rf"\b{src}\.cli:main\b"), f"{package}.cli:main"),
        (re.compile(rf"\b{src}\.api\.main\b"), f"{package}.api.main"),
        (re.compile(rf"\b(from|import)\s+{src}\b"), rf"\1 {package}"),
        (re.compile(rf"\b{src}\.(?=[a-z_]+)"), f"{package}."),
        # CLI invocations: `starter eval`, `$(BIN)/starter chat`, ...
        (re.compile(rf"\b{src} (chat|eval|eval-diff|guard|serve|init)\b"), rf"{package} \1"),
        (re.compile(rf"(\$\(BIN\)/|bin/){src}\b"), rf"\1{package}"),
    ]


class ScaffoldError(Exception):
    """A refusal with a reason the caller can act on."""


@dataclass
class ScaffoldResult:
    target: Path
    package: str
    files_copied: int = 0
    files_rewritten: list[str] = field(default_factory=list)
    residual_mentions: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        lines = [
            f"{'would create' if self.dry_run else 'created'} {self.target}",
            f"  package:   {self.package}",
            f"  files:     {self.files_copied} copied, {len(self.files_rewritten)} rewritten",
        ]
        if self.residual_mentions:
            lines.append(
                f"  prose:     {len(self.residual_mentions)} file(s) still mention "
                f"'{SOURCE_PACKAGE}' in text — review: "
                + ", ".join(self.residual_mentions[:5])
                + (" ..." if len(self.residual_mentions) > 5 else "")
            )
        lines += [
            "",
            "next:",
            f"  cd {self.target} && make install && make test",
            "  edit config/guardrails.yaml, agent/prompts.py and tools/examples.py",
        ]
        return "\n".join(lines)


def validate_package_name(name: str) -> str:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ScaffoldError(f"{name!r} is not a valid Python package name")
    if name != name.lower():
        raise ScaffoldError(f"package name must be lowercase: {name!r}")
    if name in sys.stdlib_module_names:
        raise ScaffoldError(f"{name!r} shadows a standard-library module; pick another name")
    if name == SOURCE_PACKAGE:
        raise ScaffoldError(
            f"pick a name other than {SOURCE_PACKAGE!r} — the point of init is to make it yours"
        )
    return name


def _is_text(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in {"Makefile", "Dockerfile"}


def _skip(path: Path, root: Path) -> bool:
    parts = set(path.relative_to(root).parts)
    if parts & EXCLUDE_DIRS or path.name in EXCLUDE_FILES:
        return True
    return any(part.endswith(".egg-info") for part in parts)


def rewrite(text: str, package: str) -> str:
    for pattern, replacement in _rules(package):
        text = pattern.sub(replacement, text)
    return text


def init_project(
    target: str | Path,
    package: str,
    project_name: str | None = None,
    source: str | Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> ScaffoldResult:
    """Copy the kit to `target`, renaming the package as it goes."""
    validate_package_name(package)
    root = Path(source) if source else Path(__file__).resolve().parents[2]
    if not (root / "src" / SOURCE_PACKAGE).is_dir():
        raise ScaffoldError(f"{root} does not look like the starter kit (no src/{SOURCE_PACKAGE})")

    destination = Path(target).resolve()
    if destination.exists() and any(destination.iterdir()) and not force:
        raise ScaffoldError(f"{destination} is not empty; pass force=True to write into it anyway")
    if root in destination.parents or destination == root:
        raise ScaffoldError("target must be outside the kit directory")

    result = ScaffoldResult(target=destination, package=package, dry_run=dry_run)
    name = project_name or package.replace("_", "-")

    for path in sorted(root.rglob("*")):
        if path.is_dir() or _skip(path, root):
            continue
        relative = path.relative_to(root)
        parts = [package if part == SOURCE_PACKAGE else part for part in relative.parts]
        out = destination / Path(*parts)
        result.files_copied += 1
        if dry_run:
            continue

        out.parent.mkdir(parents=True, exist_ok=True)
        if not _is_text(path):
            shutil.copy2(path, out)
            continue

        original = path.read_text(encoding="utf-8")
        updated = rewrite(original, package)
        if relative.name == "pyproject.toml":
            updated = re.sub(r'(?m)^name = "[^"]+"', f'name = "{name}"', updated, count=1)
        if updated != original:
            result.files_rewritten.append(str(relative))
        if re.search(rf"\b{SOURCE_PACKAGE}\b", updated):
            result.residual_mentions.append(str(relative))
        out.write_text(updated, encoding="utf-8")

    return result
