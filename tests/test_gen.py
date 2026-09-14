import csv
import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path

from benchlib.gen import GenError, csv_cell, data_row_counts, generate
from benchlib.model import parse_model
from tests.fixtures import SAMPLE_YAML, make_paths


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class GenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = make_paths(self.root / "a")
        self.model = parse_model(SAMPLE_YAML)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def files(self, paths) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted(paths.data.glob("*.csv"))}

    def test_byte_identical_and_seed_dependent(self) -> None:
        generate(self.paths, self.model)
        other = make_paths(self.root / "b")
        generate(other, parse_model(SAMPLE_YAML))
        self.assertEqual(self.files(self.paths), self.files(other))
        self.assertEqual(sorted(self.files(self.paths)), ["ACCT.csv", "CUST_MSTR.csv", "SEG_LKP.csv", "TXN.csv"])

        reseeded = make_paths(self.root / "c")
        generate(reseeded, parse_model(SAMPLE_YAML.replace("seed: 20240901", "seed: 7")))
        self.assertNotEqual(self.files(self.paths)["CUST_MSTR.csv"], self.files(reseeded)["CUST_MSTR.csv"])

    def test_values_follow_specs(self) -> None:
        result = generate(self.paths, self.model)
        self.assertEqual(result.rows, {"SEG_LKP": 3, "CUST_MSTR": 20, "ACCT": 50, "TXN": 200})
        segments = read_rows(self.paths.data / "SEG_LKP.csv")
        self.assertEqual([row["SEG_CD"] for row in segments], ["RE", "SM", "CO"])

        customers = read_rows(self.paths.data / "CUST_MSTR.csv")
        self.assertEqual([row["CUST_ID"] for row in customers], [str(n) for n in range(1, 21)])
        self.assertTrue(all(row["STAT_CD"] in {"A", "C", "S"} for row in customers))
        self.assertTrue(all(row["SEG_CD"] in {"", "RE", "SM", "CO"} for row in customers))
        self.assertTrue(all("2015-01-01" <= row["OPEN_DT"] <= "2024-12-31" for row in customers))

        accounts = read_rows(self.paths.data / "ACCT.csv")
        segment_of = {row["CUST_ID"]: row["SEG_CD"] for row in customers}
        self.assertTrue(all(row["CUST_ID"] in segment_of for row in accounts))
        self.assertTrue(all(row["SEG_CD"] == segment_of[row["CUST_ID"]] for row in accounts))
        self.assertTrue(all(re.fullmatch(r"\d{1,4}\.\d\d", row["BAL"]) for row in accounts), accounts[:3])

        account_ids = {row["ACCT_ID"] for row in accounts}
        for row in read_rows(self.paths.data / "TXN.csv"):
            self.assertIn(row["ACCT_ID"], account_ids)
            self.assertEqual(row["NOTE"], "big" if abs(int(row["AMT"])) > 400 else "")

    def test_zipf_prefers_first_parents(self) -> None:
        generate(self.paths, self.model)
        accounts = read_rows(self.paths.data / "ACCT.csv")
        first = sum(1 for row in accounts if row["CUST_ID"] == "1")
        last = sum(1 for row in accounts if row["CUST_ID"] == "20")
        self.assertGreater(first, last)

    def test_new_column_leaves_other_columns_unchanged(self) -> None:
        generate(self.paths, self.model)
        before = read_rows(self.paths.data / "CUST_MSTR.csv")
        text = SAMPLE_YAML.replace(
            "      - {name: STAT_CD,",
            "      - {name: RISK, type: integer}\n      - {name: STAT_CD,",
        ).replace("      STAT_CD: {choice:", "      RISK: {int: [1, 5]}\n      STAT_CD: {choice:")
        other = make_paths(self.root / "b")
        generate(other, parse_model(text))
        after = read_rows(other.data / "CUST_MSTR.csv")
        for name in ("CUST_ID", "CUST_NM", "SEG_CD", "OPEN_DT", "STAT_CD"):
            self.assertEqual([row[name] for row in before], [row[name] for row in after])

    def test_single_table_reads_parents_from_files(self) -> None:
        generate(self.paths, self.model)
        txn = (self.paths.data / "TXN.csv").read_bytes()
        (self.paths.data / "TXN.csv").unlink()
        result = generate(self.paths, self.model, ["TXN"])
        self.assertEqual(list(result.rows), ["TXN"])
        self.assertEqual((self.paths.data / "TXN.csv").read_bytes(), txn)

        (self.paths.data / "ACCT.csv").unlink()
        with self.assertRaisesRegex(GenError, "data/ACCT.csv is missing"):
            generate(self.paths, self.model, ["TXN"])
        with self.assertRaisesRegex(GenError, "unknown table"):
            generate(self.paths, self.model, ["NOPE"])

    def test_sample_rows_are_parents(self) -> None:
        text = SAMPLE_YAML.replace("    rows: 3\n", "    rows: 0\n")
        self.paths.samples.mkdir(parents=True)
        (self.paths.samples / "SEG_LKP.csv").write_text("SEG_CD,SEG_DESC\nPB,Private\nPS,Public\n", encoding="utf-8")
        (self.paths.data).mkdir(parents=True)
        (self.paths.data / "SEG_LKP.csv").write_text("stale\n", encoding="utf-8")
        result = generate(self.paths, parse_model(text))
        self.assertEqual(result.removed, [self.paths.data / "SEG_LKP.csv"])
        segments = {row["SEG_CD"] for row in read_rows(self.paths.data / "CUST_MSTR.csv")}
        self.assertLessEqual(segments, {"", "PB", "PS"})
        self.assertIn("PB", segments)

    def test_errors(self) -> None:
        cases = [
            ("CUST_NM: {faker: company}", "CUST_NM: {faker: no_such_provider}", "unknown faker provider 'no_such_provider'"),
            ("      CUST_NM: {faker: company}\n", "", "CUST_MSTR.CUST_NM is NOT NULL without a default"),
            ("NOTE: {expr: \"'big' if abs(row['AMT']) > 400 else None\"}", "NOTE: {expr: \"row['MISSING']\"}", "TXN.NOTE: expr failed on row 0: KeyError"),
        ]
        for old, new, message in cases:
            with self.subTest(message=message):
                self.assertIn(old, SAMPLE_YAML)
                with self.assertRaisesRegex(GenError, re.escape(message)):
                    generate(make_paths(self.root / "e"), parse_model(SAMPLE_YAML.replace(old, new)))

    def test_counts_and_cells(self) -> None:
        generate(self.paths, self.model)
        counts = data_row_counts(self.paths, self.model)
        self.assertEqual(counts, {"SEG_LKP": 3, "CUST_MSTR": 20, "ACCT": 50, "TXN": 200})
        self.assertEqual(csv_cell(dt.datetime(2024, 1, 2, 3, 4, 5)), "2024-01-02 03:04:05")
        self.assertEqual((csv_cell(True), csv_cell(None), csv_cell(1.5)), ("true", "", "1.5"))


if __name__ == "__main__":
    unittest.main()
