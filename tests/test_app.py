"""Smoke tests: every Streamlit page runs against the repository files without raising. Nothing is saved."""

import unittest

from streamlit.testing.v1 import AppTest

from benchlib.config import REPO_ROOT

PAGES = ["app/Home.py", "app/pages/1_Model.py", "app/pages/2_Data.py", "app/pages/3_Generate.py"]


class PagesTest(unittest.TestCase):
    def test_pages_render(self) -> None:
        for page in PAGES:
            with self.subTest(page=page):
                app = AppTest.from_file(str(REPO_ROOT / page), default_timeout=60)
                app.run()
                self.assertFalse(app.exception, [e.value for e in app.exception])
                self.assertFalse(app.error, [e.value for e in app.error])


if __name__ == "__main__":
    unittest.main()
