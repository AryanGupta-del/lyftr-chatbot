"""SQLite session storage (stdlib sqlite3, no external services)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at TEXT NOT NULL
);
"""


def _path_from_url(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("DATABASE_URL must look like sqlite:////data/app.db")
    return url[len(prefix):]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, url: str):
        self.path = _path_from_url(url)
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)  # schema is created automatically on startup

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def ping(self) -> bool:
        with self.conn() as c:
            c.execute("SELECT 1").fetchone()
        return True

    def get_system_prompt(self, session_id: str) -> str | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row["system_prompt"] if row else None

    def get_history(self, session_id: str) -> list[dict]:
        """History in the shape LiteLLM expects: [{'role', 'content'}, ...]."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def session_exists(self, session_id: str) -> bool:
        with self.conn() as c:
            row = c.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    def get_messages(self, session_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        out = []
        for r in rows:
            item = {"role": r["role"], "content": r["content"]}
            if r["metadata"]:
                item["metadata"] = json.loads(r["metadata"])
            out.append(item)
        return out

    def save_exchange(self, session_id, system_prompt, user_msg, reply, metadata):
        """Store the user message and the reply together, or not at all."""
        now = _now()
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at) VALUES (?, ?)",
                (session_id, now),
            )
            if system_prompt is not None:
                c.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                    (system_prompt, session_id),
                )
            c.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_msg, now),
            )
            c.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, reply, json.dumps(metadata), _now()),
            )
