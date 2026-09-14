import shutil
import unittest

from benchlib.diagram import dot_source, record_escape, render_svg, table_label
from benchlib.model import parse_model
from tests.fixtures import SAMPLE_YAML


class DiagramTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = parse_model(SAMPLE_YAML)

    def test_edges(self) -> None:
        source = dot_source(self.model)
        self.assertIn("ACCT -> CUST_MSTR [label=parent style=solid]", source)
        self.assertIn("TXN -> ACCT [label=parent style=dashed]", source)
        self.assertIn("CUST_MSTR -> SEG_LKP [label=lookup style=dashed]", source)
        self.assertIn("shape=record", source)

    def test_label(self) -> None:
        label = table_label(self.model.table("SEG_LKP"))
        self.assertEqual(label, "SEG_LKP|SEG_CD : char(2) PK\\lSEG_DESC : varchar(40)\\l")
        self.assertEqual(record_escape("a{b}|<c>"), "a\\{b\\}\\|\\<c\\>")

    @unittest.skipUnless(shutil.which("dot"), "graphviz dot binary not installed")
    def test_svg(self) -> None:
        svg = render_svg(self.model)
        self.assertIn(b"<svg", svg)
        self.assertIn(b"CUST_MSTR", svg)


if __name__ == "__main__":
    unittest.main()
