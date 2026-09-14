import unittest

from benchlib.build import schema_sql
from benchlib.dialects.postgres import PostgresDialect
from benchlib.model import parse_model
from tests.fixtures import SAMPLE_YAML

DIALECT = PostgresDialect()


class QuoteTest(unittest.TestCase):
    def test_rules(self) -> None:
        self.assertEqual(DIALECT.quote("CUST_MSTR"), "CUST_MSTR")
        self.assertEqual(DIALECT.quote("ORDER"), '"order"')
        self.assertEqual(DIALECT.quote("left"), '"left"')
        self.assertEqual(DIALECT.quote('odd "name"'), '"odd ""name"""')
        self.assertEqual(DIALECT.literal("it's"), "'it''s'")


class DdlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = parse_model(SAMPLE_YAML)

    def test_table_ddl(self) -> None:
        ddl = DIALECT.table_ddl(self.model.table("CUST_MSTR"), self.model.relations)
        self.assertEqual(
            ddl,
            "CREATE TABLE CUST_MSTR (\n"
            "    CUST_ID integer,\n"
            "    CUST_NM varchar(80) NOT NULL,\n"
            "    SEG_CD  char(2),\n"
            "    OPEN_DT date,\n"
            "    STAT_CD char(1) DEFAULT 'A' CHECK (STAT_CD IN ('A','C','S')),\n"
            "    PRIMARY KEY (CUST_ID)\n"
            ");",
        )

    def test_fk_and_comments(self) -> None:
        self.assertEqual(
            DIALECT.fk_ddl(self.model.relations[1]),
            "ALTER TABLE ACCT ADD FOREIGN KEY (CUST_ID) REFERENCES CUST_MSTR (CUST_ID);",
        )
        self.assertEqual(
            DIALECT.comment_sql(self.model.table("CUST_MSTR")),
            "COMMENT ON TABLE CUST_MSTR IS 'Customer master, one row per customer';\n"
            "COMMENT ON COLUMN CUST_MSTR.CUST_NM IS 'legal name';\n"
            "COMMENT ON COLUMN CUST_MSTR.SEG_CD IS 'segment code, see SEG_LKP';",
        )
        self.assertEqual(DIALECT.comment_sql(self.model.table("TXN")), "")

    def test_schema_sql_emits_only_declared_foreign_keys(self) -> None:
        sql = schema_sql(self.model, DIALECT)
        self.assertEqual(sql.count("FOREIGN KEY"), 1)
        self.assertEqual(sql.count("CREATE TABLE"), 4)
        self.assertLess(sql.index("CREATE TABLE TXN"), sql.index("ALTER TABLE"))
        self.assertNotIn("mybank.", sql)

    def test_schema_ops(self) -> None:
        self.assertEqual(DIALECT.create_schema_sql("mybank"), "CREATE SCHEMA mybank;")
        self.assertEqual(DIALECT.drop_schema_sql("mybank"), "DROP SCHEMA IF EXISTS mybank CASCADE;")
        self.assertEqual(DIALECT.qualified("mybank", "TXN"), "mybank.TXN")


if __name__ == "__main__":
    unittest.main()
