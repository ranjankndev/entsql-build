import datetime as dt
import unittest
from decimal import Decimal

from benchlib.sqlrun import QueryResult, format_table, result_summary


class FormatTest(unittest.TestCase):
    def test_table(self) -> None:
        text = format_table(
            ["cust_id", "name", "open_dt", "bal"],
            [(1, "Acme", dt.date(2020, 1, 2), Decimal("10.50")), (22, None, None, Decimal("0"))],
        )
        self.assertEqual(
            text,
            "cust_id | name | open_dt    | bal\n"
            "--------+------+------------+------\n"
            "1       | Acme | 2020-01-02 | 10.50\n"
            "22      | NULL | NULL       | 0",
        )

    def test_summary(self) -> None:
        self.assertEqual(result_summary(QueryResult(["a"], [(1,)], 0.01234, False)), "(1 row, 0.012 s)")
        self.assertEqual(result_summary(QueryResult(["a"], [(1,), (2,)], 0.5, True)), "(2 rows, truncated, 0.500 s)")


if __name__ == "__main__":
    unittest.main()
