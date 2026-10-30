import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = Path(os.getenv("SMARTHR_DB_PATH", DATA_DIR / "smarthr.db"))
ORIGINALS_DIR = DATA_DIR / "resumes" / "originals"

# shortcut: SQLite stands in for MongoDB Atlas so the API runs with zero infrastructure; swap when Atlas access is agreed.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    rubric TEXT NOT NULL DEFAULT '[]',
    rubric_version INTEGER NOT NULL DEFAULT 0,
    rubric_approved INTEGER NOT NULL DEFAULT 0,
    settings TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY,
    sha256 TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    text TEXT NOT NULL,
    chunks TEXT NOT NULL,
    embeddings BLOB,
    quality TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS applications(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    document_id INTEGER NOT NULL REFERENCES documents(id),
    status TEXT NOT NULL,
    band TEXT,
    fit_score REAL,
    coverage REAL,
    semantic REAL,
    evidence TEXT,
    rubric_version INTEGER,
    models TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(job_id, document_id));
CREATE INDEX IF NOT EXISTS ix_applications_rank ON applications(job_id, fit_score DESC);
"""


def init() -> None:
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def connect() -> sqlite3.Connection:
    # shortcut: scoring runs inside the write transaction, so concurrent uploads queue on the lock; move to a worker queue for volume.
    # FastAPI may open the connection (sync dependency) and run the endpoint on different worker threads;
    # each connection still serves exactly one request, so sharing across those threads is safe.
    conn = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_conn():
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
