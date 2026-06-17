"""Хранилище: SQLite. Диалоги, сообщения, лиды. Без внешних зависимостей."""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    ig_user_id   TEXT PRIMARY KEY,
    username     TEXT,
    mode         TEXT DEFAULT 'bot',        -- 'bot' | 'human'  (перехват менеджером)
    created_at   REAL,
    updated_at   REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ig_user_id   TEXT,
    sender       TEXT,                      -- 'guest' | 'agent' | 'manager'
    text         TEXT,
    created_at   REAL
);
CREATE TABLE IF NOT EXISTS leads (
    ig_user_id   TEXT PRIMARY KEY,
    hotel        TEXT,
    intent       TEXT,
    language     TEXT,
    check_in     TEXT,
    check_out    TEXT,
    guests       TEXT,
    room_type    TEXT,
    purpose      TEXT,
    heat         TEXT DEFAULT 'cold',       -- 'hot' | 'warm' | 'cold'
    status       TEXT DEFAULT 'new',        -- 'new' | 'qualified' | 'escalated' | 'won' | 'lost'
    summary      TEXT,
    escalated    INTEGER DEFAULT 0,
    updated_at   REAL
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)


# --- conversations ---
def get_or_create_conversation(ig_user_id: str, username: str = "") -> dict:
    now = time.time()
    with _conn() as c:
        row = c.execute("SELECT * FROM conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO conversations (ig_user_id, username, mode, created_at, updated_at) VALUES (?,?,?,?,?)",
                (ig_user_id, username, "bot", now, now),
            )
            return {"ig_user_id": ig_user_id, "username": username, "mode": "bot",
                    "created_at": now, "updated_at": now}
        return dict(row)


def set_mode(ig_user_id: str, mode: str):
    with _conn() as c:
        c.execute("UPDATE conversations SET mode=?, updated_at=? WHERE ig_user_id=?",
                  (mode, time.time(), ig_user_id))


def set_username(ig_user_id: str, username: str):
    with _conn() as c:
        c.execute("UPDATE conversations SET username=?, updated_at=? WHERE ig_user_id=?",
                  (username, time.time(), ig_user_id))


def get_conversation(ig_user_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    return dict(row) if row else None


def last_guest_ts(ig_user_id: str) -> Optional[float]:
    """Время последнего сообщения ГОСТЯ — для контроля 24-часового окна Meta."""
    with _conn() as c:
        row = c.execute(
            "SELECT MAX(created_at) AS t FROM messages WHERE ig_user_id=? AND sender='guest'",
            (ig_user_id,),
        ).fetchone()
    return row["t"] if row and row["t"] is not None else None


def list_conversations() -> list[dict]:
    """Все диалоги с превью последнего сообщения и теплотой лида (для инбокса менеджера)."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT c.ig_user_id, c.username, c.mode, c.updated_at,
                   l.heat, l.intent, l.escalated, l.status,
                   (SELECT m.text FROM messages m WHERE m.ig_user_id=c.ig_user_id
                      ORDER BY m.id DESC LIMIT 1) AS last_text,
                   (SELECT m.sender FROM messages m WHERE m.ig_user_id=c.ig_user_id
                      ORDER BY m.id DESC LIMIT 1) AS last_sender,
                   (SELECT MAX(m.created_at) FROM messages m
                      WHERE m.ig_user_id=c.ig_user_id AND m.sender='guest') AS last_guest_ts
            FROM conversations c
            LEFT JOIN leads l ON l.ig_user_id=c.ig_user_id
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(ig_user_id: str, sender: str, text: str):
    with _conn() as c:
        c.execute("INSERT INTO messages (ig_user_id, sender, text, created_at) VALUES (?,?,?,?)",
                  (ig_user_id, sender, text, time.time()))
        c.execute("UPDATE conversations SET updated_at=? WHERE ig_user_id=?", (time.time(), ig_user_id))


def get_history(ig_user_id: str, limit: int = 20) -> list[dict]:
    """Последние сообщения в хронологическом порядке (для контекста модели)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT sender, text FROM messages WHERE ig_user_id=? ORDER BY id DESC LIMIT ?",
            (ig_user_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- leads ---
def upsert_lead(ig_user_id: str, fields: dict):
    """Обновляет только переданные непустые поля лида."""
    fields = {k: v for k, v in fields.items() if v not in (None, "", [])}
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=excluded.{k}" for k in fields)
    keys = ", ".join(["ig_user_id"] + list(fields))
    placeholders = ", ".join(["?"] * (len(fields) + 1))
    values = [ig_user_id] + list(fields.values())
    with _conn() as c:
        c.execute(
            f"INSERT INTO leads ({keys}) VALUES ({placeholders}) "
            f"ON CONFLICT(ig_user_id) DO UPDATE SET {cols}",
            values,
        )


def get_lead(ig_user_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM leads WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    return dict(row) if row else None


def reset_conversation(ig_user_id: str):
    """Полностью очистить диалог и лид (для интерактивной демки — кнопка 'Новый диалог')."""
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE ig_user_id=?", (ig_user_id,))
        c.execute("DELETE FROM leads WHERE ig_user_id=?", (ig_user_id,))
        c.execute("DELETE FROM conversations WHERE ig_user_id=?", (ig_user_id,))


def list_leads() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT l.*, c.username, c.mode FROM leads l "
            "LEFT JOIN conversations c ON c.ig_user_id=l.ig_user_id "
            "ORDER BY l.updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
