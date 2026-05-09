# -*- coding: utf-8 -*-
import sqlite3
import os
import threading
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "human-api.db")

_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            model TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'waiting',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
    """)
    conn.commit()


def save_session(s):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, model, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (s["id"], s.get("model", ""), s.get("status", "waiting"),
         s.get("created_at", ""), s.get("updated_at", ""))
    )
    conn.commit()


def save_message(session_id, role, content, created_at=None):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, role, content, created_at or datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit()


def save_messages_bulk(session_id, messages):
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    now = datetime.now().isoformat(timespec="seconds")
    for msg in messages:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, msg.get("role", ""), msg.get("content", ""), now)
        )
    conn.commit()


def load_all_sessions():
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    return [{"id": r["id"], "model": r["model"], "status": r["status"],
             "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]


def load_messages(session_id):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,)
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def get_session(session_id):
    conn = _get_conn()
    r = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "model": r["model"], "status": r["status"],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def update_session_status(session_id, status, updated_at=None):
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
        (status, updated_at or datetime.now().isoformat(timespec="seconds"), session_id)
    )
    conn.commit()


def delete_session(session_id):
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def clear_all():
    conn = _get_conn()
    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM sessions")
    conn.commit()


def get_history(session_id, limit=20):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def export_session_messages(session_id):
    session = get_session(session_id)
    if not session:
        return None, None
    return session, load_messages(session_id)


def export_all_messages():
    conn = _get_conn()
    sessions = conn.execute("SELECT * FROM sessions ORDER BY created_at ASC").fetchall()
    result = []
    for s in sessions:
        messages = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
            (s["id"],)
        ).fetchall()
        result.append({
            "session": {"id": s["id"], "model": s["model"], "status": s["status"],
                        "created_at": s["created_at"], "updated_at": s["updated_at"]},
            "messages": [{"role": m["role"], "content": m["content"],
                          "created_at": m["created_at"]} for m in messages]
        })
    return result
