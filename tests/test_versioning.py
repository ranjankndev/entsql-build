import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path

from benchlib.dialects.postgres import PostgresDialect
from benchlib.model import load_model, parse_model
from benchlib.versioning import changelog_line, commit_version, diff_models
from tests.fixtures import SAMPLE_YAML, make_paths

DAY = dt.date(2026, 9, 14)


class DiffTest(unittest.TestCase):
    def test_diff_and_line(self) -> None:
        old = parse_model(SAMPLE_YAML)
        new = parse_model(
            SAMPLE_YAML.replace("      - {name: NOTE, type: text}\n", "      - {name: MEMO, type: text}\n")
        )
        del new.tables["SEG_LKP"]
        diff = diff_models(old, new)
        self.assertEqual(diff.removed_tables, ["SEG_LKP"])
        self.assertEqual(diff.added_columns, ["TXN.MEMO"])
        self.assertEqual(diff.removed_columns, ["TXN.NOTE"])
        self.assertEqual(
            changelog_line(4, "rename note", diff, DAY),
            "- v004 (2026-09-14) rename note; removed tables: SEG_LKP; added columns: TXN.MEMO; removed columns: TXN.NOTE",
        )
        self.assertEqual(
            changelog_line(1, "init", diff_models(None, old), DAY),
            "- v001 (2026-09-14) init; added tables: SEG_LKP, CUST_MSTR, ACCT, TXN",
        )


@unittest.skipUnless(shutil.which("dot"), "graphviz dot binary not installed")
class CommitTest(unittest.TestCase):
    def test_commit_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.model.parent.mkdir(parents=True)
            paths.model.write_text(SAMPLE_YAML.replace("version: 3", "version: 0"), encoding="utf-8")
            first = commit_version(paths, PostgresDialect(), "init", DAY, git=False)
            self.assertEqual(first.version, 1)
            self.assertTrue((paths.versions / "mybank_v001.yaml").exists())
            self.assertTrue((paths.versions / "mybank_v001.sql").exists())
            self.assertEqual(load_model(paths.model).version, 1)
            self.assertIn("model version 1", paths.ddl.read_text())

            text = paths.model.read_text().replace("      - {name: NOTE, type: text}\n", "      - {name: NOTE, type: text}\n      - {name: MEMO, type: text}\n")
            paths.model.write_text(text)
            second = commit_version(paths, PostgresDialect(), "add memo", DAY, git=False)
            self.assertEqual(second.changelog_line, "- v002 (2026-09-14) add memo; added columns: TXN.MEMO")
            changelog = (paths.versions / "CHANGELOG.md").read_text()
            self.assertEqual(changelog.count("\n- v00"), 2)


if __name__ == "__main__":
    unittest.main()
