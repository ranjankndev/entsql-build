import math
import unittest

import pandas as pd

from benchlib import editor
from benchlib.model import parse_model, validate_model
from tests.fixtures import SAMPLE_YAML


class CellTest(unittest.TestCase):
    def test_clean(self) -> None:
        for empty in (None, math.nan, pd.NA, "", "  "):
            self.assertIsNone(editor.clean(empty))
        self.assertEqual(editor.clean(" x "), "x")
        self.assertTrue(editor.as_bool(None, True))
        self.assertFalse(editor.as_bool("false", True))


class RecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = parse_model(SAMPLE_YAML)

    def test_frames_round_trip(self) -> None:
        table = self.model.table("CUST_MSTR")
        columns, errors = editor.columns_from_records("CUST_MSTR", editor.frame_records(editor.column_frame(table)))
        self.assertEqual((columns, errors), (table.columns, []))
        generation, errors = editor.generation_from_records(
            "CUST_MSTR", editor.frame_records(editor.generation_frame(table))
        )
        self.assertEqual((generation, errors), (table.generation, []))
        relations, errors = editor.relations_from_records(editor.frame_records(editor.relation_frame(self.model)))
        self.assertEqual((relations, errors), (self.model.relations, []))

    def test_empty_table_frame_has_editor_dtypes(self) -> None:
        data = editor.frame([], editor.COLUMN_FIELDS)
        self.assertEqual(str(data.dtypes["pk"]), "bool")
        self.assertEqual(str(data.dtypes["name"]), "string")

    def test_column_rows(self) -> None:
        records = [
            {"name": "ID", "type": "integer", "pk": True, "nullable": True},
            {"name": None, "type": None, "pk": False},
            {"name": "X", "type": None},
        ]
        columns, errors = editor.columns_from_records("T", records)
        self.assertEqual([(c.name, c.pk, c.nullable) for c in columns], [("ID", True, False)])
        self.assertEqual(errors, ["T column row 3: name and type are both required"])

    def test_generation_rows(self) -> None:
        records = [{"column": "A", "spec": "{seq: 1}"}, {"column": "B", "spec": "seq 1"}, {"column": "C", "spec": "{a: 1"}]
        generation, errors = editor.generation_from_records("T", records)
        self.assertEqual(generation, {"A": {"seq": 1}})
        self.assertEqual(len(errors), 2)
        self.assertIn("spec must be a mapping", errors[0])

    def test_relation_rows(self) -> None:
        relations, errors = editor.relations_from_records([{"from": "A.X", "to": "B"}, {"from": "A.X", "to": "B.Y"}])
        self.assertEqual(errors, ["relation row 1: from and to must be TABLE.COLUMN"])
        self.assertEqual((relations[0].kind, relations[0].declared), ("parent", False))


class TableOperationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = parse_model(SAMPLE_YAML)

    def test_add_and_delete(self) -> None:
        updated, errors = editor.add_table(self.model, "ATM")
        self.assertEqual(errors, [])
        self.assertNotIn("ATM", self.model.tables)
        self.assertEqual([c.name for c in updated.tables["ATM"].pk_columns], ["ATM_ID"])
        self.assertEqual(validate_model(updated), [])
        self.assertEqual(editor.add_table(self.model, "cust_mstr")[1], ["table cust_mstr already exists"])
        self.assertEqual(len(editor.add_table(self.model, "bad name")[1]), 1)

        remaining = editor.delete_table(self.model, "ACCT")
        self.assertNotIn("ACCT", remaining.tables)
        self.assertEqual([r.from_ref for r in remaining.relations], ["CUST_MSTR.SEG_CD"])

    def test_rename_follows_relations_and_specs(self) -> None:
        updated, errors = editor.rename_table(self.model, "CUST_MSTR", "CUSTOMER")
        self.assertEqual(errors, [])
        self.assertEqual(list(updated.tables)[1], "CUSTOMER")
        self.assertIn("CUSTOMER.CUST_ID", [r.to_ref for r in updated.relations])
        acct = updated.tables["ACCT"].generation
        self.assertEqual(acct["CUST_ID"]["ref"], "CUSTOMER.CUST_ID")
        self.assertEqual(acct["SEG_CD"]["copy"]["from"], "CUSTOMER.SEG_CD")
        self.assertEqual(validate_model(updated), [])

    def test_apply_edits(self) -> None:
        table = self.model.table("SEG_LKP")
        columns = editor.column_records(table) + [{"name": "SORT_NO", "type": "smallint"}]
        relations = editor.relation_records(self.model) + [{"from": "SEG_LKP.SORT_NO", "to": "TXN.TXN_ID", "declared": False, "kind": "demo"}]
        edit = editor.TableEdit("SEGMENT", "Segments", 5, columns, editor.generation_records(table))
        updated, errors = editor.apply_edits(self.model, "SEG_LKP", edit, relations)
        self.assertEqual(errors, [])
        segment = updated.tables["SEGMENT"]
        self.assertEqual((segment.rows, segment.description, segment.columns[-1].name), (5, "Segments", "SORT_NO"))
        self.assertEqual(updated.relations[-1].from_ref, "SEGMENT.SORT_NO")
        self.assertEqual(updated.relations[0].to_ref, "SEGMENT.SEG_CD")
        self.assertIn("SEG_LKP", self.model.tables)

        bad = editor.TableEdit("SEG_LKP", None, 1, [{"name": "X"}], [])
        unchanged, errors = editor.apply_edits(self.model, "SEG_LKP", bad, [])
        self.assertIs(unchanged, self.model)
        self.assertEqual(errors, ["SEG_LKP column row 1: name and type are both required"])


if __name__ == "__main__":
    unittest.main()
