import tempfile
import unittest
from pathlib import Path

from benchlib.config import REPO_ROOT, ConfigError, load_config, load_dotenv, parse_dotenv, select_name

MINIMAL_TOML = """
[paths]
model = "model/mybank.yaml"
extra_sql = "model/extra.sql"
samples = "model/samples"
build = "build"
versions = "versions"
data = "data"
checks = "checks/generated"
llm_logs = "logs/llm"

[db.local]
dialect = "postgres"
host = "127.0.0.1"
port = "5432"
dbname = "benchdata"
owner_user = "bench_owner"
read_user = "bench_read"

[db.broken]
dialect = "postgres"

[llm.none]
provider = "none"

[llm.openrouter]
provider = "openai_compat"
model = "some/model"
api_key_env = "OPENROUTER_API_KEY"
"""


class ParseDotenvTest(unittest.TestCase):
    def test_pairs_comments_quotes_and_export(self) -> None:
        text = "# comment\n\nA=1\nexport B = two \nC=\"x=y\"\nD='q'\nnot a pair\nE=\n"
        self.assertEqual(parse_dotenv(text), {"A": "1", "B": "two", "C": "x=y", "D": "q", "E": ""})

    def test_existing_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("A=from_file\nB=from_file\n", encoding="utf-8")
            environ = {"A": "from_env"}
            load_dotenv(path, environ)
        self.assertEqual(environ, {"A": "from_env", "B": "from_file"})

    def test_missing_file_is_ignored(self) -> None:
        environ: dict[str, str] = {}
        load_dotenv(Path("/nonexistent/.env"), environ)
        self.assertEqual(environ, {})


class SelectNameTest(unittest.TestCase):
    def test_flag_then_env_then_default(self) -> None:
        self.assertEqual(select_name("vps", "BENCH_DB", "local", {"BENCH_DB": "other"}), "vps")
        self.assertEqual(select_name(None, "BENCH_DB", "local", {"BENCH_DB": "other"}), "other")
        self.assertEqual(select_name(None, "BENCH_DB", "local", {}), "local")


class LoadConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "bench.toml").write_text(MINIMAL_TOML, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_defaults(self) -> None:
        config = load_config(self.root, environ={})
        self.assertEqual(config.db.name, "local")
        self.assertEqual(config.db.port, 5432)
        self.assertEqual(config.db.owner_user, "bench_owner")
        self.assertEqual(config.llm.provider, "none")
        self.assertEqual(config.paths.build_stamp, self.root / "build" / ".build.json")

    def test_dotenv_selects_llm_profile(self) -> None:
        (self.root / ".env").write_text("BENCH_LLM=openrouter\n", encoding="utf-8")
        environ: dict[str, str] = {}
        config = load_config(self.root, environ=environ)
        self.assertEqual(config.llm.provider, "openai_compat")
        self.assertEqual(config.llm.settings["api_key_env"], "OPENROUTER_API_KEY")
        self.assertEqual(environ["BENCH_LLM"], "openrouter")

    def test_unknown_profile(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unknown db profile 'nope'"):
            load_config(self.root, db="nope", environ={})

    def test_incomplete_profile(self) -> None:
        with self.assertRaisesRegex(ConfigError, r"\[db.broken\] is missing: host"):
            load_config(self.root, db="broken", environ={})

    def test_missing_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "not found"):
            load_config(self.root / "missing", environ={})

    def test_repo_bench_toml_has_all_planned_profiles(self) -> None:
        for db in ("local", "vps"):
            for llm in ("none", "openrouter", "ollama", "anthropic"):
                config = load_config(REPO_ROOT, db=db, llm=llm, environ={})
                self.assertEqual(config.db.dialect, "postgres")
                self.assertEqual(config.llm.name, llm)


if __name__ == "__main__":
    unittest.main()
