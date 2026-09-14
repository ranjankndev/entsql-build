"""Connections for the selected database profile.

No password is ever passed or stored here: libpq reads it from ~/.pgpass.
"""

from __future__ import annotations

import sys
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from benchlib.config import DbProfile

APPLICATION_NAME = "bench"
CONNECT_TIMEOUT_SECONDS = 10


def conninfo(profile: DbProfile, user: str) -> str:
    """libpq connection string for one role of the profile, always over TCP."""
    return make_conninfo(
        host=profile.host,
        port=profile.port,
        dbname=profile.dbname,
        user=user,
        application_name=APPLICATION_NAME,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def connect_owner(profile: DbProfile) -> psycopg.Connection:
    """Connection as the owner role; used for rebuilds."""
    return psycopg.connect(conninfo(profile, profile.owner_user))


def connect_read(profile: DbProfile) -> psycopg.Connection:
    """Connection as the read-only role, with read-only transactions; used for checks and ad-hoc SQL."""
    conn = psycopg.connect(conninfo(profile, profile.read_user))
    conn.read_only = True
    return conn


def execute(conn: psycopg.Connection, sql: str, params: Any = None, verbose: bool = False) -> psycopg.Cursor:
    """Run one statement; with verbose, print it to stderr first."""
    if verbose:
        print(sql.strip(), file=sys.stderr)
    return conn.execute(sql, params)
