"""`starter init`: the generated project must stand on its own.

The strongest test here is the end-to-end one — scaffold a project into a temp
directory and run *its* test suite. A scaffolder that produces a project whose
own tests fail is worse than no scaffolder.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from starter.scaffold import (
    SOURCE_PACKAGE,
    ScaffoldError,
    init_project,
    rewrite,
    validate_package_name,
)

KIT = Path(__file__).resolve().parents[1]
# A project scaffolded from the kit runs these same tests, and cannot scaffold
# itself under its own name — so the target name is derived, never hardcoded.
NEW = f"{SOURCE_PACKAGE}child"


# ------------------------------------------------------------- name checks


def test_rejects_invalid_names():
    for bad in ("2bot", "my-bot", "class", "MyBot", "json", SOURCE_PACKAGE):
        with pytest.raises(ScaffoldError):
            validate_package_name(bad)


def test_accepts_a_sensible_name():
    assert validate_package_name(NEW) == NEW


# ---------------------------------------------------------------- rewriting


@pytest.mark.parametrize(
    "template",
    [
        "from {pkg}.agent import Agent",
        "import {pkg}",
        "{pkg}.cli:main",
        "uvicorn {pkg}.api.main:app",
        'pythonpath = ["src/{pkg}"]',
        "$(BIN)/{pkg} eval --suite x",
        '{pkg} chat "hello"',
    ],
)
def test_rewrites_identifiers_and_invocations(template):
    before = template.format(pkg=SOURCE_PACKAGE)
    assert rewrite(before, NEW) == template.format(pkg=NEW)


def test_leaves_prose_alone():
    prose = f"This {SOURCE_PACKAGE} kit ships guardrails."
    assert rewrite(prose, NEW) == prose


# ------------------------------------------------------------------ copying


def test_refuses_a_non_empty_target(tmp_path):
    (tmp_path / "existing.txt").write_text("hi")
    with pytest.raises(ScaffoldError):
        init_project(tmp_path, package=NEW)


def test_force_writes_into_a_non_empty_target(tmp_path):
    (tmp_path / "existing.txt").write_text("hi")
    result = init_project(tmp_path, package=NEW, force=True)
    assert result.files_copied > 0


def test_refuses_to_write_inside_the_kit():
    with pytest.raises(ScaffoldError):
        init_project(KIT / "nested", package=NEW)


def test_dry_run_writes_nothing(tmp_path):
    result = init_project(tmp_path, package=NEW, dry_run=True)
    assert result.files_copied > 0
    assert not any(tmp_path.iterdir())


def test_excludes_local_state_and_secrets(tmp_path):
    init_project(tmp_path, package=NEW)
    for unwanted in (".git", ".venv", "__pycache__", ".env", ".eval-runs"):
        assert not (tmp_path / unwanted).exists()


def test_renames_the_package_directory_and_metadata(tmp_path):
    init_project(tmp_path, package=NEW, project_name="support-bot")
    assert (tmp_path / "src" / NEW / "agent" / "loop.py").is_file()
    assert not (tmp_path / "src" / SOURCE_PACKAGE).exists()
    assert 'name = "support-bot"' in (tmp_path / "pyproject.toml").read_text()
    assert f"{NEW}.cli:main" in (tmp_path / "pyproject.toml").read_text()


def test_no_python_file_still_imports_the_old_package(tmp_path):
    init_project(tmp_path, package=NEW)
    # Word boundary matters: `import starterchild` is not `import starter`.
    stale = re.compile(rf"\b(?:import|from)\s+{re.escape(SOURCE_PACKAGE)}\b")
    offenders = [
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*.py")
        if stale.search(path.read_text())
    ]
    assert offenders == []


# --------------------------------------------------------------- end to end


# A scaffolded project runs this same suite. Without this guard the end-to-end
# tests would scaffold-and-run recursively, forever — which is exactly what
# happened the first time they were written.
NESTED = "STARTER_SCAFFOLD_NESTED"
skip_when_nested = pytest.mark.skipif(
    os.environ.get(NESTED) == "1",
    reason="running inside a scaffolded project; would recurse",
)


def _child_env(tmp_path: Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "src"), NESTED: "1"}


@pytest.mark.slow
@skip_when_nested
def test_the_generated_project_passes_its_own_tests(tmp_path):
    init_project(tmp_path, package=NEW)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-2000:]


@pytest.mark.slow
@skip_when_nested
def test_the_generated_project_answers_a_query(tmp_path):
    init_project(tmp_path, package=NEW)
    completed = subprocess.run(
        [sys.executable, "-m", f"{NEW}.cli", "chat", "hello"],
        cwd=tmp_path,
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "hello" in completed.stdout
