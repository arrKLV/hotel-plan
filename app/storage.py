"""Хранилище диалогов, сообщений и лидов.

Один и тот же код работает с двумя бэкендами:
  - SQLite  (локально / тесты)      — если DATABASE_URL не задан;
  - Postgres (прод, напр. Neon)     — если DATABASE_URL начинается с postgres://

Выбор бэкенда — по config.DATABASE_URL. SQL пишем с плейсхолдером "?",
для Postgres он автоматически превращается в "%s".
"""
import time
from contextlib import contextmanager
from typing import Optional

import config

_IS_PG = config.DATABASE_URL.startswith(("postgres://", "postgresql://"))

if _IS_PG:
    import psycopg
    from psycopg.rows import dict_row

# id: SQLite — AUTOINCREMENT, Postgres — SERIAL; REAL → double precision
_ID = "SERIAL PRIMARY KEY" if _IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
_TS = "double precision" if _IS_PG else "REAL"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS conversations (
    ig_user_id   TEXT PRIMARY KEY,
    username     TEXT,
    mode         TEXT DEFAULT 'bot',
    created_at   {_TS},
    updated_at   {_TS}
);
CREATE TABLE IF NOT EXISTS messages (
    id           {_ID},
    ig_user_id   TEXT,
    sender       TEXT,
    text         TEXT,
    created_at   {_TS}
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
    heat         TEXT DEFAULT 'cold',
    status       TEXT DEFAULT 'new',
    summary      TEXT,
    escalated    INTEGER DEFAULT 0,
    updated_at   {_TS}
);
"""


def _q(sql: str) -> str:
    """Плейсхолдеры: SQLite ждёт '?', Postgres — '%s'."""
    return sql.replace("?", "%s") if _IS_PG else sql


@contextmanager
def _conn():
    if _IS_PG:
        conn = psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
    else:
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _exec(c, sql: str, params=()):
    return c.execute(_q(sql), params)


def init_db():
    with _conn() as c:
        for stmt in filter(lambda s: s.strip(), SCHEMA.split(";")):
            c.execute(stmt)


# --- conversations ---
def get_or_create_conversation(ig_user_id: str, username: str = "") -> dict:
    now = time.time()
    with _conn() as c:
        row = _exec(c,"SELECT * FROM conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
        if row is None:
            _exec(c,
                "INSERT INTO conversations (ig_user_id, username, mode, created_at, updated_at) VALUES (?,?,?,?,?)",
                (ig_user_id, username, "bot", now, now),
            )
            return {"ig_user_id": ig_user_id, "username": username, "mode": "bot",
                    "created_at": now, "updated_at": now}
        return dict(row)


def set_mode(ig_user_id: str, mode: str):
    with _conn() as c:
        _exec(c, "UPDATE conversations SET mode=?, updated_at=? WHERE ig_user_id=?",
              (mode, time.time(), ig_user_id))


def set_username(ig_user_id: str, username: str):
    with _conn() as c:
        _exec(c, "UPDATE conversations SET username=?, updated_at=? WHERE ig_user_id=?",
              (username, time.time(), ig_user_id))


def get_conversation(ig_user_id: str) -> Optional[dict]:
    with _conn() as c:
        row = _exec(c,"SELECT * FROM conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    return dict(row) if row else None


def last_guest_ts(ig_user_id: str) -> Optional[float]:
    """Время последнего сообщения ГОСТЯ — для контроля 24-часового окна Meta."""
    with _conn() as c:
        row = _exec(c,
            "SELECT MAX(created_at) AS t FROM messages WHERE ig_user_id=? AND sender='guest'",
            (ig_user_id,),
        ).fetchone()
    return row["t"] if row and row["t"] is not None else None


def list_conversations() -> list[dict]:
    """Все диалоги с превью последнего сообщения и теплотой лида (для инбокса менеджера)."""
    with _conn() as c:
        rows = _exec(c,
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
        _exec(c, "INSERT INTO messages (ig_user_id, sender, text, created_at) VALUES (?,?,?,?)",
              (ig_user_id, sender, text, time.time()))
        _exec(c, "UPDATE conversations SET updated_at=? WHERE ig_user_id=?", (time.time(), ig_user_id))


def get_history(ig_user_id: str, limit: int = 20) -> list[dict]:
    """Последние сообщения в хронологическом порядке (для контекста модели)."""
    with _conn() as c:
        rows = _exec(c,
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
        _exec(c,
            f"INSERT INTO leads ({keys}) VALUES ({placeholders}) "
            f"ON CONFLICT(ig_user_id) DO UPDATE SET {cols}",
            values,
        )


def get_lead(ig_user_id: str) -> Optional[dict]:
    with _conn() as c:
        row = _exec(c,"SELECT * FROM leads WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    return dict(row) if row else None


def reset_conversation(ig_user_id: str):
    """Полностью очистить диалог и лид (для интерактивной демки — кнопка 'Новый диалог')."""
    with _conn() as c:
        _exec(c, "DELETE FROM messages WHERE ig_user_id=?", (ig_user_id,))
        _exec(c, "DELETE FROM leads WHERE ig_user_id=?", (ig_user_id,))
        _exec(c, "DELETE FROM conversations WHERE ig_user_id=?", (ig_user_id,))


def list_leads() -> list[dict]:
    with _conn() as c:
        rows = _exec(c,
            "SELECT l.*, c.username, c.mode FROM leads l "
            "LEFT JOIN conversations c ON c.ig_user_id=l.ig_user_id "
            "ORDER BY l.updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
