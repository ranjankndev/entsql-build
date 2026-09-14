import tempfile
import unittest
from pathlib import Path

from benchlib.model import (
    ModelError,
    dump_model,
    generation_order,
    load_model,
    parse_model,
    save_model,
    validate_model,
)
from tests.fixtures import SAMPLE_YAML


def replace(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


class ParseTest(unittest.TestCase):
    def test_sample_parses_and_is_valid(self) -> None:
        model = parse_model(SAMPLE_YAML)
        self.assertEqual((model.schema, model.version, model.seed), ("mybank", 3, 20240901))
        self.assertEqual(list(model.tables), ["SEG_LKP", "CUST_MSTR", "ACCT", "TXN"])
        cust = model.table("CUST_MSTR")
        self.assertEqual([c.name for c in cust.pk_columns], ["CUST_ID"])
        self.assertFalse(cust.column("CUST_ID").nullable)
        self.assertEqual(cust.column("STAT_CD").default, "'A'")
        self.assertEqual(model.relations[2].note, "dropped FK on purpose, ERP style")
        self.assertEqual(validate_model(model), [])

    def test_dump_round_trip(self) -> None:
        model = parse_model(SAMPLE_YAML)
        text = dump_model(model)
        self.assertIn("  - {name: CUST_ID, type: integer, pk: true}", text)
        self.assertIn("""default: "'A'", check: "STAT_CD IN ('A','C','S')\"""", text)
        self.assertEqual(parse_model(text), model)

    def test_save_and_load(self) -> None:
        model = parse_model(SAMPLE_YAML)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.yaml"
            save_model(model, path)
            self.assertEqual(load_model(path), model)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["m.yaml"])

    def test_structural_errors(self) -> None:
        text = replace(SAMPLE_YAML, "nullable: false, description: legal name", "nulable: false")
        text = replace(text, "to: SEG_LKP.SEG_CD,", "to: SEG_LKP,")
        with self.assertRaises(ModelError) as caught:
            parse_model(text)
        self.assertIn("table CUST_MSTR column CUST_NM: unknown key 'nulable'", caught.exception.errors)
        self.assertTrue(any("from and to must be TABLE.COLUMN" in e for e in caught.exception.errors))

    def test_duplicate_yaml_key(self) -> None:
        text = replace(SAMPLE_YAML, "    rows: 3\n", "    rows: 3\n    rows: 4\n")
        with self.assertRaisesRegex(ModelError, "duplicate key 'rows'"):
            parse_model(text)


class ValidateTest(unittest.TestCase):
    def errors_for(self, old: str, new: str) -> list[str]:
        return validate_model(parse_model(replace(SAMPLE_YAML, old, new)))

    def test_unknown_ref(self) -> None:
        self.assertIn(
            "CUST_MSTR.SEG_CD generation: ref: unknown table SEG_LOOKUP",
            self.errors_for("ref: SEG_LKP.SEG_CD", "ref: SEG_LOOKUP.SEG_CD"),
        )

    def test_duplicate_column(self) -> None:
        errors = self.errors_for("{name: OPEN_DT,   type: date}", "{name: cust_nm, type: date}")
        self.assertTrue(any("duplicate column cust_nm" in e for e in errors), errors)

    def test_relation_column_missing(self) -> None:
        errors = self.errors_for("to: CUST_MSTR.CUST_ID", "to: CUST_MSTR.CUSTOMER_ID")
        self.assertIn("relation ACCT.CUST_ID -> CUST_MSTR.CUSTOMER_ID: unknown column CUST_MSTR.CUSTOMER_ID", errors)

    def test_declared_relation_needs_primary_key_target(self) -> None:
        errors = self.errors_for(
            "{from: ACCT.CUST_ID,     to: CUST_MSTR.CUST_ID, declared: true",
            "{from: ACCT.CUST_ID,     to: CUST_MSTR.OPEN_DT, declared: true",
        )
        self.assertTrue(any("single-column primary key of CUST_MSTR" in e for e in errors), errors)

    def test_generation_spec_errors(self) -> None:
        cases = [
            ("{seq: 1}", "{seq: 1, faker: name}", "needs exactly one of"),
            ("{int: [-500, 500]}", "{int: [500, -500]}", "int needs [lo, hi]"),
            ('{date: ["2015-01-01", "2024-12-31"]}', '{date: ["2015-13-01", "2024-12-31"]}', "date needs [from, to]"),
            ("{choice: {A: 0.8, C: 0.15, S: 0.05}}", "{choice: {A: -1}}", "choice weights"),
            ("via: CUST_ID}", "via: BAL}", "no relation from ACCT.BAL to CUST_MSTR"),
            ("null_rate: 0.1", "null_rate: 2", "null_rate must be a number between 0 and 1"),
            ("dist: zipf", "dist: normal", "dist is only allowed"),
            ("abs(row['AMT']) > 400", "abs(row['AMT'] > 400", "expr does not compile"),
            ("{name: ACCT_ID, type: bigint, pk: true}", "{name: ACCT_ID, type: bigint}", ""),
        ]
        for old, new, expected in cases[:-1]:
            with self.subTest(new=new):
                errors = self.errors_for(old, new)
                self.assertTrue(any(expected in e for e in errors), errors)

    def test_null_rate_on_required_column(self) -> None:
        errors = self.errors_for("CUST_NM: {faker: company}", "CUST_NM: {faker: company, null_rate: 0.5}")
        self.assertIn("CUST_MSTR.CUST_NM generation: null_rate on a NOT NULL or primary key column", errors)

    def test_unknown_generation_column(self) -> None:
        errors = self.errors_for("CUST_NM: {faker: company}", "CUST_NAME: {faker: company}")
        self.assertIn("CUST_MSTR.CUST_NAME generation: unknown column", errors)


class GenerationOrderTest(unittest.TestCase):
    def test_parents_first(self) -> None:
        order = generation_order(parse_model(SAMPLE_YAML))
        self.assertLess(order.index("SEG_LKP"), order.index("CUST_MSTR"))
        self.assertLess(order.index("CUST_MSTR"), order.index("ACCT"))
        self.assertLess(order.index("ACCT"), order.index("TXN"))

    def test_cycle(self) -> None:
        text = SAMPLE_YAML + "  - {from: SEG_LKP.SEG_CD, to: TXN.TXN_ID, kind: parent}\n"
        with self.assertRaisesRegex(ModelError, "cycle between: SEG_LKP, CUST_MSTR, ACCT, TXN"):
            generation_order(parse_model(text))


if __name__ == "__main__":
    unittest.main()
