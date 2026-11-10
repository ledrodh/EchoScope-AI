"""SQLite connection manager with strict foreign key enforcement and WAL mode."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from echoscope.config import get_settings


def configure_sqlite_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Configures SQLite pragmas for integrity, concurrency, and usability."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def get_db_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Creates a configured SQLite connection. Defaults to path from Settings."""
    if db_path is None:
        db_path = get_settings().db_path

    path_str = str(db_path)
    if path_str != ":memory:":
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path_str, timeout=30.0, check_same_thread=False)
    return configure_sqlite_connection(conn)


@contextmanager
def transaction(db_path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager providing an atomic transaction with automatic rollback on exception."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
