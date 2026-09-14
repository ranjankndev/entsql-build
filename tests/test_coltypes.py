import unittest

from benchlib.coltypes import ColumnType, json_schema, normalize_value, parse_type
from benchlib.model import Column


def check(type_: str, value: object, required: bool = False) -> tuple[str | None, str | None]:
    return normalize_value(Column("C", type_, nullable=not required), value)


class ParseTypeTest(unittest.TestCase):
    def test_types(self) -> None:
        self.assertEqual(parse_type("INTEGER"), ColumnType("integer", "integer"))
        self.assertEqual(parse_type("int8"), ColumnType("integer", "bigint"))
        self.assertEqual(parse_type("numeric(12, 2)"), ColumnType("decimal", "numeric", precision=12, scale=2))
        self.assertEqual(parse_type("numeric(5)"), ColumnType("decimal", "numeric", precision=5, scale=0))
        self.assertEqual(parse_type("numeric"), ColumnType("decimal", "numeric"))
        self.assertEqual(parse_type("char"), ColumnType("char", "char", length=1))
        self.assertEqual(parse_type("character varying(10)"), ColumnType("varchar", "character varying", length=10))
        self.assertEqual(parse_type("timestamp with time zone").kind, "timestamp")
        self.assertEqual(parse_type("integer[]").kind, "text")
        self.assertEqual(parse_type("jsonb").kind, "text")


class NormalizeTest(unittest.TestCase):
    def test_null(self) -> None:
        self.assertEqual(check("integer", None), (None, None))
        self.assertEqual(check("integer", "", required=True), (None, "must not be NULL"))

    def test_integer(self) -> None:
        self.assertEqual(check("integer", " 12"), ("12", None))
        self.assertEqual(check("bigint", 7.0), ("7", None))
        self.assertEqual(check("integer", 1.5)[1], "expected an integer, got 1.5")
        self.assertEqual(check("integer", True)[1], "expected an integer, got True")
        self.assertEqual(check("smallint", 40000)[1], "40000 is out of range for smallint")

    def test_decimal(self) -> None:
        self.assertEqual(check("numeric(12,2)", 10.5), ("10.50", None))
        self.assertEqual(check("numeric(12,2)", "-0.01"), ("-0.01", None))
        self.assertEqual(check("numeric(12,2)", "1.234")[1], "1.234 has more than 2 decimal places")
        self.assertEqual(check("numeric(4,2)", "123.4")[1], "123.4 does not fit numeric(4,2)")
        self.assertEqual(check("numeric(4,2)", "99.99"), ("99.99", None))
        self.assertEqual(check("numeric", "abc")[1], "expected a number, got 'abc'")

    def test_text_and_dates(self) -> None:
        self.assertEqual(check("char(2)", "RE "), ("RE", None))
        self.assertEqual(check("char(2)", "RET")[1], "'RET' is longer than 2 characters")
        self.assertEqual(check("varchar(3)", 123), ("123", None))
        self.assertEqual(check("date", "2024-02-29"), ("2024-02-29", None))
        self.assertEqual(check("date", "2023-02-29")[1], "expected a date as YYYY-MM-DD, got '2023-02-29'")
        self.assertEqual(check("timestamp", "2024-01-02T03:04:05"), ("2024-01-02 03:04:05", None))
        self.assertEqual(check("boolean", "Yes"), ("true", None))
        self.assertEqual(check("boolean", "maybe")[1], "expected true or false, got 'maybe'")

    def test_json_schema(self) -> None:
        self.assertEqual(json_schema(Column("ID", "integer", pk=True, nullable=False))["type"], "integer")
        schema = json_schema(Column("D", "date", check="D > '2000-01-01'", description="open date"))
        self.assertEqual(schema["type"], ["string", "null"])
        self.assertEqual(schema["description"], "date; YYYY-MM-DD; CHECK (D > '2000-01-01'); open date")


if __name__ == "__main__":
    unittest.main()
