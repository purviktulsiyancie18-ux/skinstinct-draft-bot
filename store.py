"""SQLite store for notes and drafts.

Note lifecycle:  new -> discarded | held | queued -> drafted
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

import config

# Postgres on Vercel (Neon sets DATABASE_URL / POSTGRES_URL); SQLite locally.
PG_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id            {pk},
    source        TEXT NOT NULL,              -- 'dm', 'channel', 'backlog'
    source_ref    TEXT UNIQUE,                -- dedupe key, e.g. chat:message_id
    text          TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new',
    score         INTEGER,
    triage_json   TEXT,
    ack_msg_id    BIGINT                      -- bot's reply, so Meera can reply to it
);
CREATE TABLE IF NOT EXISTS drafts (
    id            {pk},
    note_id       INTEGER NOT NULL REFERENCES notes(id),
    created_at    TEXT NOT NULL,
    reference_json TEXT,
    body          TEXT NOT NULL,
    issues_json   TEXT,
    feedback      TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Pg:
    """Just enough of the sqlite3 connection interface over psycopg."""

    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        pass  # autocommit

    def close(self):
        self._conn.close()


def connect():
    if PG_URL:
        conn = _Pg(PG_URL)
        for stmt in SCHEMA.format(pk="SERIAL PRIMARY KEY").split(";"):
            if stmt.strip():
                conn.execute(stmt)
        return conn
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT"))
    return conn


def add_note(conn, source, source_ref, text, received_at=None):
    """Returns the new note id, or None if this message was already stored."""
    row = conn.execute(
        "INSERT INTO notes (source, source_ref, text, received_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (source_ref) DO NOTHING RETURNING id",
        (source, source_ref, text.strip(), received_at or now_iso()),
    ).fetchone()
    conn.commit()
    return row["id"] if row else None


def claim(conn, key):
    """True the first time a key is seen - used to ignore Telegram webhook retries."""
    row = conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT (k) DO NOTHING RETURNING k",
                       (key, now_iso())).fetchone()
    conn.commit()
    return row is not None


def get_note(conn, note_id):
    return conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()


def note_by_ack(conn, ack_msg_id):
    return conn.execute("SELECT * FROM notes WHERE ack_msg_id = ?", (ack_msg_id,)).fetchone()


def set_triage(conn, note_id, status, score, triage):
    conn.execute(
        "UPDATE notes SET status = ?, score = ?, triage_json = ? WHERE id = ?",
        (status, score, json.dumps(triage), note_id),
    )
    conn.commit()


def set_status(conn, note_id, status):
    conn.execute("UPDATE notes SET status = ? WHERE id = ?", (status, note_id))
    conn.commit()


def set_ack(conn, note_id, msg_id):
    conn.execute("UPDATE notes SET ack_msg_id = ? WHERE id = ?", (msg_id, note_id))
    conn.commit()


def append_to_note(conn, note_id, extra):
    conn.execute("UPDATE notes SET text = text || ? WHERE id = ?", ("\n\n" + extra.strip(), note_id))
    conn.commit()


def queued(conn, limit=20):
    # Strongest ideas first; among equals, the freshest (timeliness decays).
    return conn.execute(
        "SELECT * FROM notes WHERE status = 'queued' ORDER BY score DESC, received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def add_draft(conn, note_id, reference, body, issues, feedback=None):
    row = conn.execute(
        "INSERT INTO drafts (note_id, created_at, reference_json, body, issues_json, feedback) "
        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        (note_id, now_iso(), json.dumps(reference), body, json.dumps(issues), feedback),
    ).fetchone()
    conn.execute("UPDATE notes SET status = 'drafted' WHERE id = ?", (note_id,))
    conn.commit()
    return row["id"]


def latest_draft(conn, note_id):
    return conn.execute(
        "SELECT * FROM drafts WHERE note_id = ? ORDER BY id DESC LIMIT 1", (note_id,)
    ).fetchone()


def counts(conn):
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM notes GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (key, str(value)))
    conn.commit()
