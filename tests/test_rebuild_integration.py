"""Rebuild against the real database profile. Run with BENCH_INTEGRATION=1; uses a throwaway schema."""

import datetime as dt
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from benchlib.build import BuildError, build_status, rebuild, render
from benchlib.checks import check_database
from benchlib.config import load_config
from benchlib.db import connect_owner
from benchlib.dialects.postgres import PostgresDialect
from benchlib.model import parse_model
from benchlib.sqlrun import run_sql
from tests.fixtures import SAMPLE_YAML, make_paths

NOW = dt.datetime(2026, 9, 14, 12, 0, tzinfo=dt.UTC)


@unittest.skipUnless(os.environ.get("BENCH_INTEGRATION") == "1", "set BENCH_INTEGRATION=1 to run against the database")
@unittest.skipUnless(shutil.which("dot"), "graphviz dot binary not installed")
class RebuildIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_config(environ={}).db
        self.schema = f"bench_it_{os.getpid()}"
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = make_paths(Path(self.tmp.name))
        for directory in (self.paths.model.parent, self.paths.samples, self.paths.data):
            directory.mkdir(parents=True, exist_ok=True)
        self.dialect = PostgresDialect()

    def tearDown(self) -> None:
        with connect_owner(self.profile) as conn:
            conn.execute(self.dialect.drop_schema_sql(self.schema))
        self.tmp.cleanup()

    def write_model(self, text: str) -> None:
        self.paths.model.write_text(text.replace("schema: mybank", f"schema: {self.schema}"), encoding="utf-8")
        render(parse_model(self.paths.model.read_text()), self.paths, self.dialect)

    def test_rebuild_rollback_and_samples(self) -> None:
        self.write_model(SAMPLE_YAML)
        (self.paths.samples / "SEG_LKP.csv").write_text("SEG_CD,SEG_DESC\nRE,Retail\nSM,Small business\n", encoding="utf-8")
        (self.paths.data / "CUST_MSTR.csv").write_text("CUST_ID,CUST_NM,SEG_CD\n1,Acme,RE\n2,Beta,\n", encoding="utf-8")
        result = rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        self.assertEqual(result.upserted, {"SEG_LKP": 2})
        self.assertEqual(result.loaded, {"CUST_MSTR": 2})
        self.assertEqual(result.stamp.row_counts["CUST_MSTR"], 2)
        self.assertEqual(build_status(self.paths).state, "ok")

        query = run_sql(self.paths, self.profile, "select cust_nm, stat_cd, seg_cd from cust_mstr order by cust_id")
        self.assertEqual(query.rows, [("Acme", "A", "RE"), ("Beta", "A", None)])

        # A sample row updates the generated row with the same primary key.
        (self.paths.samples / "CUST_MSTR.csv").write_text("CUST_ID,CUST_NM\n2,Beta Renamed\n3,Gamma\n", encoding="utf-8")
        rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        names = run_sql(self.paths, self.profile, "select cust_nm from cust_mstr order by cust_id").rows
        self.assertEqual(names, [("Acme",), ("Beta Renamed",), ("Gamma",)])

        # A typo in a CHECK fails inside the transaction and leaves the previous schema intact.
        self.write_model(SAMPLE_YAML.replace("STAT_CD IN ('A','C','S')", "STAT_CDX IN ('A','C','S')"))
        with self.assertRaisesRegex(BuildError, "stat_cdx"):
            rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        names = run_sql(self.paths, self.profile, "select count(*) from cust_mstr").rows
        self.assertEqual(names, [(3,)])
        self.assertEqual(build_status(self.paths).state, "stale")

    def test_declared_foreign_key_to_sample_only_parent(self) -> None:
        self.write_model(SAMPLE_YAML)
        (self.paths.samples / "CUST_MSTR.csv").write_text("CUST_ID,CUST_NM\n7,Sample Customer\n", encoding="utf-8")
        (self.paths.data / "ACCT.csv").write_text("ACCT_ID,CUST_ID,BAL\n1,7,10.00\n", encoding="utf-8")
        result = rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        self.assertEqual((result.upserted, result.loaded), ({"CUST_MSTR": 1}, {"ACCT": 1}))

    def test_orphan_sample_fails_structural_checks(self) -> None:
        self.write_model(SAMPLE_YAML)
        (self.paths.samples / "SEG_LKP.csv").write_text("SEG_CD,SEG_DESC\nRE,Retail\n", encoding="utf-8")
        (self.paths.samples / "CUST_MSTR.csv").write_text("CUST_ID,CUST_NM,SEG_CD\n1,Acme,RE\n", encoding="utf-8")
        rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        self.assertTrue((self.paths.checks / "structural.sql").is_file())
        self.assertEqual(check_database(self.paths, self.profile).failed, [])

        (self.paths.samples / "CUST_MSTR.csv").write_text("CUST_ID,CUST_NM,SEG_CD\n1,Acme,RE\n2,Orphan,ZZ\n", encoding="utf-8")
        with self.assertRaises(BuildError) as caught:
            rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        self.assertEqual(str(caught.exception), "1 structural check(s) failed")
        self.assertIn("FAIL orphan CUST_MSTR.SEG_CD -> SEG_LKP.SEG_CD: 1 rows", caught.exception.details)
        self.assertEqual(run_sql(self.paths, self.profile, "select count(*) from cust_mstr").rows, [(1,)])

    def test_bad_csv_rolls_back(self) -> None:
        self.write_model(SAMPLE_YAML)
        (self.paths.data / "TXN.csv").write_text("TXN_ID,ACCT_ID,AMT\n1,1,not-a-number\n", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "loading data/TXN.csv"):
            rebuild(self.paths, self.profile, self.dialect, NOW, generate=False)
        with connect_owner(self.profile) as conn:
            exists = conn.execute("select count(*) from pg_namespace where nspname = %s", (self.schema,)).fetchone()[0]
        self.assertEqual(exists, 0)


if __name__ == "__main__":
    unittest.main()
