"""SQLite store for notes and drafts.

Note lifecycle:  new -> discarded | held | queued -> drafted
"""
import json
import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,              -- 'dm', 'channel', 'backlog'
    source_ref    TEXT UNIQUE,                -- dedupe key, e.g. chat:message_id
    text          TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new',
    score         INTEGER,
    triage_json   TEXT,
    ack_msg_id    INTEGER                     -- bot's reply, so Meera can reply to it
);
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_note(conn, source, source_ref, text, received_at=None):
    """Returns the new note id, or None if this message was already stored."""
    try:
        cur = conn.execute(
            "INSERT INTO notes (source, source_ref, text, received_at) VALUES (?, ?, ?, ?)",
            (source, source_ref, text.strip(), received_at or now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


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
    cur = conn.execute(
        "INSERT INTO drafts (note_id, created_at, reference_json, body, issues_json, feedback) VALUES (?, ?, ?, ?, ?, ?)",
        (note_id, now_iso(), json.dumps(reference), body, json.dumps(issues), feedback),
    )
    conn.execute("UPDATE notes SET status = 'drafted' WHERE id = ?", (note_id,))
    conn.commit()
    return cur.lastrowid


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
