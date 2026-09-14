import unittest

from psycopg.conninfo import conninfo_to_dict

from benchlib.config import DbProfile
from benchlib.db import conninfo

PROFILE = DbProfile(
    name="local",
    dialect="postgres",
    host="127.0.0.1",
    port=5432,
    dbname="benchdata",
    owner_user="bench_owner",
    read_user="bench_read",
)


class ConninfoTest(unittest.TestCase):
    def test_tcp_without_password(self) -> None:
        parts = conninfo_to_dict(conninfo(PROFILE, PROFILE.read_user))
        self.assertEqual(parts["host"], "127.0.0.1")
        self.assertEqual(parts["port"], "5432")
        self.assertEqual(parts["dbname"], "benchdata")
        self.assertEqual(parts["user"], "bench_read")
        self.assertNotIn("password", parts)


if __name__ == "__main__":
    unittest.main()
