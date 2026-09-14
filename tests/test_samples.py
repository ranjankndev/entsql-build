import tempfile
import unittest
from pathlib import Path
from typing import Any

from benchlib import samples
from benchlib.model import parse_model
from benchlib.samples import SamplesError
from tests.fixtures import SAMPLE_YAML, make_paths


class ScriptedProvider:
    """Returns a fixed reply and remembers what it was asked."""

    name = "scripted"

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        return self.reply


def customer(cust_id: int, seg: str | None, name: str = "Acme GmbH") -> dict[str, Any]:
    return {"CUST_ID": cust_id, "CUST_NM": name, "SEG_CD": seg, "OPEN_DT": "2020-05-01", "STAT_CD": "A"}


class SamplesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = make_paths(Path(self.tmp.name))
        self.paths.samples.mkdir(parents=True)
        self.paths.data.mkdir(parents=True)
        self.model = parse_model(SAMPLE_YAML)
        (self.paths.samples / "SEG_LKP.csv").write_text("SEG_CD,SEG_DESC\nRE,Retail\nSM,Small business\n", encoding="utf-8")
        (self.paths.data / "SEG_LKP.csv").write_text("SEG_CD,SEG_DESC\nCO,Corporate\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fill_appends_valid_rows_and_rejects_bad_ones(self) -> None:
        reply = {"rows": [customer(1, "RE"), customer(2, None), customer(3, "XX"), customer(4, "CO"), customer(5, "SM")]}
        provider = ScriptedProvider(reply)
        result = samples.fill_samples(self.paths, self.model, "CUST_MSTR", "3 customers, one with a NULL segment", 3, provider)

        self.assertEqual([row["CUST_ID"] for row in result.appended], ["1", "2", "4"])
        self.assertEqual(result.extra_valid_rows, 1)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].number, 3)
        self.assertEqual(
            result.rejected[0].problems,
            ["SEG_CD: 'XX' does not exist in SEG_LKP.SEG_CD (existing values include: CO, RE, SM)"],
        )
        self.assertEqual(
            result.path.read_text(encoding="utf-8"),
            "CUST_ID,CUST_NM,SEG_CD,OPEN_DT,STAT_CD\n1,Acme GmbH,RE,2020-05-01,A\n2,Acme GmbH,,2020-05-01,A\n4,Acme GmbH,CO,2020-05-01,A\n",
        )

        system, user, schema = provider.calls[0]
        self.assertIn("existing values: RE, SM, CO", user)
        self.assertIn("Return exactly 3 new rows.", user)
        self.assertIn("STAT_CD char(1) [DEFAULT 'A', CHECK (STAT_CD IN ('A','C','S'))]", user)
        self.assertEqual(schema["properties"]["rows"]["items"]["required"], ["CUST_ID", "CUST_NM", "SEG_CD", "OPEN_DT", "STAT_CD"])

    def test_second_fill_sees_existing_rows_and_rejects_duplicate_keys(self) -> None:
        samples.fill_samples(self.paths, self.model, "CUST_MSTR", "one", 1, ScriptedProvider({"rows": [customer(1, "RE")]}))
        provider = ScriptedProvider({"rows": [customer(1, "SM"), customer(2, "SM", name="x" * 81), {"CUST_ID": 9, "BOGUS": 1}]})
        result = samples.fill_samples(self.paths, self.model, "CUST_MSTR", "more", 2, provider)
        self.assertEqual(result.appended, [])
        problems = [problem for rejected in result.rejected for problem in rejected.problems]
        self.assertIn("primary key (1) already exists in the sample rows", problems)
        self.assertIn(f"CUST_NM: '{'x' * 81}' is longer than 80 characters", problems)
        self.assertIn("unknown column BOGUS", problems)
        self.assertIn("CUST_NM: must not be NULL", problems)
        self.assertIn('"CUST_ID": "1"', provider.calls[0][1])

    def test_file_with_fewer_columns(self) -> None:
        path = self.paths.samples / "CUST_MSTR.csv"
        path.write_text("CUST_ID,CUST_NM\n1,Old", encoding="utf-8")
        result = samples.fill_samples(self.paths, self.model, "CUST_MSTR", "one", 1, ScriptedProvider({"rows": [customer(2, "RE")]}))
        self.assertEqual(result.dropped_columns, ["SEG_CD", "OPEN_DT", "STAT_CD"])
        self.assertEqual(path.read_text(encoding="utf-8"), "CUST_ID,CUST_NM\n1,Old\n2,Acme GmbH\n")

    def test_errors(self) -> None:
        with self.assertRaisesRegex(SamplesError, "unknown table NOPE"):
            samples.fill_samples(self.paths, self.model, "NOPE", "x", 1, ScriptedProvider({"rows": []}))
        with self.assertRaisesRegex(SamplesError, "no rows list"):
            samples.fill_samples(self.paths, self.model, "SEG_LKP", "x", 1, ScriptedProvider({"data": []}))
        (self.paths.samples / "TXN.csv").write_text("TXN_ID,WHAT\n", encoding="utf-8")
        with self.assertRaisesRegex(SamplesError, "header has columns that TXN does not have: WHAT"):
            samples.fill_samples(self.paths, self.model, "TXN", "x", 1, ScriptedProvider({"rows": []}))

    def test_save_records_validates_everything(self) -> None:
        records = [
            {"SEG_CD": "RE", "SEG_DESC": "Retail"},
            {"SEG_CD": None, "SEG_DESC": None},
            {"SEG_CD": "PB", "SEG_DESC": "Private"},
            {"SEG_CD": "PB", "SEG_DESC": "Again"},
        ]
        result = samples.save_records(self.paths, self.model, "SEG_LKP", records)
        self.assertEqual(result.rejected[0].problems, ["primary key (PB) already exists in the sample rows"])
        self.assertIn("SM,Small business", (self.paths.samples / "SEG_LKP.csv").read_text())

        result = samples.save_records(self.paths, self.model, "SEG_LKP", records[:3])
        self.assertEqual(result.rejected, [])
        self.assertEqual((self.paths.samples / "SEG_LKP.csv").read_text(), "SEG_CD,SEG_DESC\nRE,Retail\nPB,Private\n")

    def test_frames(self) -> None:
        frame = samples.sample_frame(self.paths, self.model.table("ACCT"))
        self.assertEqual(list(frame.columns), ["ACCT_ID", "CUST_ID", "SEG_CD", "BAL"])
        preview = samples.data_preview(self.paths, self.model.table("SEG_LKP"))
        self.assertEqual(preview.to_dict("records"), [{"SEG_CD": "CO", "SEG_DESC": "Corporate"}])


if __name__ == "__main__":
    unittest.main()
