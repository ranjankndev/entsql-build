import unittest

from tools.bench import HANDLERS, PLANNED_STEP, build_parser, command_key


class ParserTest(unittest.TestCase):
    def test_every_planned_command_parses(self) -> None:
        argv_by_key = {
            "model import": ["model", "import", "mybank.sql"],
            "model render": ["model", "render"],
            "model commit": ["model", "commit", "-m", "init"],
            "rebuild": ["rebuild", "--no-generate"],
            "status": ["status"],
            "samples fill": ["samples", "fill", "CUST_MSTR", "3 customers", "-n", "3"],
            "gen": ["gen", "CUST_MSTR", "ACCT"],
            "check": ["check"],
            "sql": ["sql", "select 1"],
        }
        parser = build_parser()
        for key, argv in argv_by_key.items():
            with self.subTest(key=key):
                self.assertEqual(command_key(parser.parse_args(["--db", "vps", *argv])), key)
        self.assertEqual(set(PLANNED_STEP) | set(HANDLERS), set(argv_by_key))
        self.assertEqual(set(PLANNED_STEP) & set(HANDLERS), set())

    def test_arguments(self) -> None:
        args = build_parser().parse_args(["--verbose", "--llm", "ollama", "samples", "fill", "T", "x", "-n", "3"])
        self.assertTrue(args.verbose)
        self.assertEqual((args.llm, args.table, args.instruction, args.n), ("ollama", "T", "x", 3))


if __name__ == "__main__":
    unittest.main()
