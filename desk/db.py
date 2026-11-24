"""Database connection and small query helpers.

Uses whichever PostgreSQL driver is installed: psycopg (version 3) or
psycopg2. Only the standard Python database interface (DB-API 2.0) is used,
so either works. Connection settings come from the usual PostgreSQL
environment variables (PGHOST, PGPORT, PGUSER, PGPASSWORD); the database name
is PGDATABASE, or notes_desk if that isn't set.
"""

import importlib
import os

from . import DeskError

DEFAULT_DATABASE = "notes_desk"
_driver = None


def driver():
    global _driver
    if _driver is None:
        names = [os.environ["NOTES_DESK_DRIVER"]] if os.environ.get("NOTES_DESK_DRIVER") else ["psycopg", "psycopg2"]
        for name in names:
            try:
                _driver = importlib.import_module(name)
                break
            except ImportError:
                continue
        else:
            raise DeskError(
                "The PostgreSQL driver for Python isn't installed.\n"
                "  Fedora:        sudo dnf install python3-psycopg3\n"
                "  Other systems: python3 -m pip install \"psycopg[binary]\""
            )
    return _driver


def database_name():
    return os.environ.get("PGDATABASE") or DEFAULT_DATABASE


def connect(dbname=None, autocommit=False):
    name = dbname or database_name()
    try:
        conn = driver().connect(dbname=name)
    except driver().Error as e:
        msg = error_message(e)
        if "does not exist" in msg:
            raise DeskError(f"Database {name} doesn't exist yet. Run: python3 notes_desk.py setup")
        raise DeskError(f"Couldn't connect to PostgreSQL: {msg}\n"
                        "Check that the server is running (systemctl status postgresql).")
    conn.autocommit = autocommit
    return conn


def is_db_error(e):
    return isinstance(e, driver().Error)


def error_message(e):
    """The server's message, without the driver's extra lines."""
    diag = getattr(e, "diag", None)
    msg = getattr(diag, "message_primary", None) if diag else None
    return (msg or str(e)).strip()


def run(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def run_many(conn, sql, rows):
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def query(conn, sql, params=None):
    """Returns (column names, rows)."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return [], []
        return [d[0] for d in cur.description], cur.fetchall()


def rows(conn, sql, params=None):
    return query(conn, sql, params)[1]


def scalar(conn, sql, params=None):
    result = rows(conn, sql, params)
    return result[0][0] if result else None


def run_file(conn, path):
    """Runs a .sql file (several statements, no parameters)."""
    with conn.cursor() as cur:
        cur.execute(path.read_text())
