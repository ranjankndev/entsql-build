import unittest

from benchlib.build import schema_sql
from benchlib.config import REPO_ROOT
from benchlib.dialects.postgres import PostgresDialect
from benchlib.importer import import_ddl
from benchlib.model import validate_model

DDL = """
CREATE SCHEMA IF NOT EXISTS shop;
SET search_path TO shop;
CREATE TABLE shop.CUSTOMER (
    CUST_ID   integer PRIMARY KEY,
    "MixedName" varchar(80) NOT NULL,
    STAT_CD   char(1) DEFAULT 'A' CHECK (STAT_CD IN ('A', 'C')),
    REGION    char(2),
    CODE      text UNIQUE
);
CREATE TABLE ORDERS (
    ORDER_ID  bigint NOT NULL,
    LINE_NO   smallint NOT NULL,
    CUST_ID   integer REFERENCES customer (cust_id),
    AMOUNT    numeric(14,2) NOT NULL DEFAULT 0,
    SHIP_DT   date,
    DUE_DT    date,
    PRIMARY KEY (ORDER_ID, LINE_NO),
    CHECK (AMOUNT >= 0),
    CHECK (DUE_DT >= SHIP_DT)
);
CREATE TABLE REGION_LKP (REGION char(2) PRIMARY KEY, NAME varchar(40));
ALTER TABLE ONLY Customer ADD CONSTRAINT cust_region_fk FOREIGN KEY (region) REFERENCES REGION_LKP;
COMMENT ON TABLE CUSTOMER IS 'Customers, it''s the master';
COMMENT ON COLUMN shop.customer.stat_cd IS 'A active, C closed';
CREATE INDEX orders_cust ON ORDERS (CUST_ID);
"""


class ImportDdlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = import_ddl(DDL)
        self.model = self.result.model

    def test_tables_and_names_keep_source_spelling(self) -> None:
        self.assertEqual(self.model.schema, "shop")
        self.assertEqual(self.model.version, 0)
        self.assertEqual(list(self.model.tables), ["CUSTOMER", "ORDERS", "REGION_LKP"])
        customer = self.model.table("CUSTOMER")
        self.assertEqual([c.name for c in customer.columns], ["CUST_ID", "MixedName", "STAT_CD", "REGION", "CODE"])

    def test_columns(self) -> None:
        customer = self.model.table("CUSTOMER")
        stat = customer.column("STAT_CD")
        self.assertEqual((stat.type, stat.default, stat.check), ("char(1)", "'A'", "stat_cd IN ('A', 'C')"))
        self.assertEqual(stat.description, "A active, C closed")
        self.assertTrue(customer.column("CUST_ID").pk)
        self.assertFalse(customer.column("MixedName").nullable)
        self.assertEqual(customer.description, "Customers, it's the master")
        orders = self.model.table("ORDERS")
        self.assertEqual([c.name for c in orders.pk_columns], ["ORDER_ID", "LINE_NO"])
        self.assertEqual(orders.column("AMOUNT").type, "numeric(14, 2)")
        self.assertEqual(orders.column("AMOUNT").check, "amount >= 0")

    def test_declared_relations(self) -> None:
        pairs = [(r.from_ref, r.to_ref, r.declared) for r in self.model.relations]
        self.assertEqual(
            pairs,
            [("ORDERS.CUST_ID", "CUSTOMER.CUST_ID", True), ("CUSTOMER.REGION", "REGION_LKP.REGION", True)],
        )

    def test_warnings(self) -> None:
        warnings = "\n".join(self.result.warnings)
        self.assertIn("CUSTOMER.CODE: skipped CONSTR_UNIQUE constraint", warnings)
        self.assertIn("ORDERS: skipped CHECK (due_dt >= ship_dt)", warnings)
        self.assertIn("skipped statement: CREATE INDEX orders_cust", warnings)
        self.assertNotIn("SET", warnings)
        self.assertEqual(self.result.errors, [])

    def test_round_trip_through_emit(self) -> None:
        emitted = schema_sql(self.model, PostgresDialect())
        again = import_ddl(emitted, schema="shop")
        self.assertEqual(again.model, self.model)

    def test_starter_model_imports_cleanly(self) -> None:
        sql = (REPO_ROOT / "model" / "import" / "mybank.sql").read_text(encoding="utf-8")
        result = import_ddl(sql)
        self.assertEqual(result.warnings, [])
        self.assertEqual(validate_model(result.model), [])
        self.assertEqual(len(result.model.tables), 7)
        self.assertEqual(len(result.model.relations), 3)


if __name__ == "__main__":
    unittest.main()
