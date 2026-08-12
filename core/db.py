"""SQLite persistence.

Chosen over an in-memory store because the feedback agent runs in a *different*
browser session from the owner dashboard — a customer clicking their link is a
separate Streamlit session with its own memory. Anything shared between the two
has to outlive a single session, and a file-backed DB is the smallest thing that
does that without adding a service to run.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback.db"

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL,
    product       TEXT    NOT NULL,
    extra         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email, product)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    subject       TEXT    NOT NULL,
    body          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per (customer, campaign). `token` is what the emailed link carries,
-- so it is the only thing that identifies a feedback session.
CREATE TABLE IF NOT EXISTS invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id),
    customer_id   INTEGER NOT NULL REFERENCES customers(id),
    token         TEXT    NOT NULL UNIQUE,
    status        TEXT    NOT NULL DEFAULT 'invited',  -- invited|opened|in_progress|completed
    sent_at       TEXT,
    opened_at     TEXT,
    completed_at  TEXT,
    UNIQUE (campaign_id, customer_id)
);

-- One row per answered question. Incremental writes are what make an abandoned
-- session resumable: whatever was answered is already durable.
CREATE TABLE IF NOT EXISTS answers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_id     INTEGER NOT NULL REFERENCES invites(id),
    question_idx  INTEGER NOT NULL,
    question      TEXT    NOT NULL,
    answer        TEXT    NOT NULL,
    probed        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (invite_id, question_idx)
);

-- Analysis is cached per invite so the dashboard never re-scores on every render
-- (and never re-bills an LLM call for a response that has not changed).
CREATE TABLE IF NOT EXISTS insights (
    invite_id     INTEGER PRIMARY KEY REFERENCES invites(id),
    sentiment     TEXT    NOT NULL,
    score         REAL    NOT NULL,
    themes        TEXT    NOT NULL,
    summary       TEXT    NOT NULL,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- UNIQUE(invite_id) is the guarantee of "one reward per completed response".
-- It is enforced by the database, not by application logic, so a double-submit
-- or a concurrent request cannot mint a second gift card.
CREATE TABLE IF NOT EXISTS rewards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_id     INTEGER NOT NULL UNIQUE REFERENCES invites(id),
    code          TEXT    NOT NULL UNIQUE,
    amount        INTEGER NOT NULL,
    currency      TEXT    NOT NULL DEFAULT 'INR',
    issued_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Every email is recorded whether or not SMTP is configured, so the flow is
-- demonstrable (and auditable) with no mail server.
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    to_email      TEXT    NOT NULL,
    subject       TEXT    NOT NULL,
    body          TEXT    NOT NULL,
    kind          TEXT    NOT NULL,       -- invite|reward
    delivered     INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    """One connection per thread — Streamlit reruns can cross threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid
