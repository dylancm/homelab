"""SQLite connection + schema bootstrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults; ensure schema is applied."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn
