import tempfile
import unittest
from pathlib import Path

from benchlib.build import (
    NO_BUILD,
    BuildError,
    BuildStamp,
    build_status,
    compare_files,
    file_hashes,
    read_stamp,
    rendered_ddl,
    schema_sql,
    status_text,
    unused_csv_files,
    write_stamp,
)
from benchlib.dialects.postgres import PostgresDialect
from benchlib.model import parse_model
from tests.fixtures import SAMPLE_YAML, make_paths


class CompareTest(unittest.TestCase):
    def test_changes(self) -> None:
        built = {"a": "1", "b": "2", "c": "3"}
        current = {"a": "1", "b": "9", "d": "4"}
        self.assertEqual(compare_files(built, current), ["b (changed)", "c (removed)", "d (added)"])


class StatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = make_paths(Path(self.tmp.name))
        for directory in (self.paths.model.parent, self.paths.samples, self.paths.data):
            directory.mkdir(parents=True, exist_ok=True)
        self.paths.model.write_text(SAMPLE_YAML, encoding="utf-8")
        (self.paths.data / "TXN.csv").write_text("TXN_ID\n1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def stamp(self) -> BuildStamp:
        return BuildStamp("2026-09-14T10:00:00+00:00", "local", "mybank", 3, file_hashes(self.paths), {"TXN": 1})

    def test_no_build(self) -> None:
        status = build_status(self.paths)
        self.assertEqual((status.state, status_text(status)), ("none", NO_BUILD))

    def test_ok_then_stale(self) -> None:
        write_stamp(self.paths.build_stamp, self.stamp())
        self.assertEqual(read_stamp(self.paths.build_stamp), self.stamp())
        status = build_status(self.paths)
        self.assertEqual(status.state, "ok")
        self.assertTrue(status_text(status).startswith("OK: built 2026-09-14T10:00:00+00:00 on db profile local"))

        self.paths.model.write_text(SAMPLE_YAML + "\n", encoding="utf-8")
        (self.paths.samples / "SEG_LKP.csv").write_text("SEG_CD\nRE\n", encoding="utf-8")
        (self.paths.data / "TXN.csv").unlink()
        status = build_status(self.paths)
        self.assertEqual(status.state, "stale")
        self.assertEqual(
            status.changes,
            ["data/TXN.csv (removed)", "model/mybank.yaml (changed)", "model/samples/SEG_LKP.csv (added)"],
        )
        self.assertIn("STALE:", status_text(status))

    def test_rendered_ddl_must_match(self) -> None:
        model = parse_model(SAMPLE_YAML)
        with self.assertRaisesRegex(BuildError, "run ./bench model render first"):
            rendered_ddl(self.paths, model, PostgresDialect())
        self.paths.build.mkdir()
        self.paths.ddl.write_text(schema_sql(model, PostgresDialect()), encoding="utf-8")
        self.assertIn("CREATE TABLE TXN", rendered_ddl(self.paths, model, PostgresDialect()))

    def test_unused_csv_files(self) -> None:
        (self.paths.data / "OLD_TABLE.csv").write_text("X\n", encoding="utf-8")
        self.assertEqual(
            unused_csv_files(self.paths, parse_model(SAMPLE_YAML)),
            ["data/OLD_TABLE.csv matches no table in the model, not loaded"],
        )


if __name__ == "__main__":
    unittest.main()
