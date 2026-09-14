"""Load bench.toml and the optional .env file, and select the db and llm profiles."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "local"
DEFAULT_LLM = "none"
DB_KEYS = ("dialect", "host", "port", "dbname", "owner_user", "read_user")
PATH_KEYS = ("model", "extra_sql", "samples", "build", "versions", "data", "checks", "llm_logs")


class ConfigError(Exception):
    """bench.toml is missing, malformed, or does not contain the selected profile."""


@dataclass(frozen=True)
class DbProfile:
    name: str
    dialect: str
    host: str
    port: int
    dbname: str
    owner_user: str
    read_user: str


@dataclass(frozen=True)
class LlmProfile:
    name: str
    provider: str
    settings: dict[str, Any] = field(default_factory=dict)  # base_url, model, api_key_env, ...


@dataclass(frozen=True)
class Paths:
    root: Path
    model: Path
    extra_sql: Path
    samples: Path
    build: Path
    versions: Path
    data: Path
    checks: Path
    llm_logs: Path

    @property
    def build_stamp(self) -> Path:
        return self.build / ".build.json"

    @property
    def ddl(self) -> Path:
        return self.build / f"{self.model.stem}.sql"

    @property
    def diagram(self) -> Path:
        return self.build / f"{self.model.stem}.svg"


@dataclass(frozen=True)
class Config:
    paths: Paths
    db: DbProfile
    llm: LlmProfile


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines and # comments are skipped, one pair of quotes is stripped."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(path: Path, environ: MutableMapping[str, str]) -> None:
    """Copy .env values into environ; variables already set in the environment win."""
    if not path.is_file():
        return
    for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
        environ.setdefault(key, value)


def select_name(explicit: str | None, env_var: str, default: str, environ: Mapping[str, str]) -> str:
    """Command-line flag first, then the environment variable, then the default."""
    return explicit or environ.get(env_var) or default


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def profile_section(data: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    profiles = data.get(kind, {})
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "none defined"
        raise ConfigError(f"unknown {kind} profile '{name}' in bench.toml (known: {known})")
    return profiles[name]


def parse_db_profile(name: str, section: dict[str, Any]) -> DbProfile:
    missing = [key for key in DB_KEYS if key not in section]
    if missing:
        raise ConfigError(f"[db.{name}] is missing: {', '.join(missing)}")
    values = {key: section[key] for key in DB_KEYS}
    values["port"] = int(values["port"])
    return DbProfile(name=name, **values)


def parse_llm_profile(name: str, section: dict[str, Any]) -> LlmProfile:
    if "provider" not in section:
        raise ConfigError(f"[llm.{name}] is missing: provider")
    settings = {key: value for key, value in section.items() if key != "provider"}
    return LlmProfile(name=name, provider=section["provider"], settings=settings)


def parse_paths(root: Path, section: dict[str, Any]) -> Paths:
    missing = [key for key in PATH_KEYS if key not in section]
    if missing:
        raise ConfigError(f"[paths] is missing: {', '.join(missing)}")
    return Paths(root=root, **{key: root / section[key] for key in PATH_KEYS})


def load_config(
    root: Path = REPO_ROOT,
    db: str | None = None,
    llm: str | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> Config:
    """Read root/.env into environ (os.environ by default), then bench.toml, then pick profiles."""
    env = os.environ if environ is None else environ
    load_dotenv(root / ".env", env)
    data = read_toml(root / "bench.toml")
    db_name = select_name(db, "BENCH_DB", DEFAULT_DB, env)
    llm_name = select_name(llm, "BENCH_LLM", DEFAULT_LLM, env)
    return Config(
        paths=parse_paths(root, data.get("paths", {})),
        db=parse_db_profile(db_name, profile_section(data, "db", db_name)),
        llm=parse_llm_profile(llm_name, profile_section(data, "llm", llm_name)),
    )
