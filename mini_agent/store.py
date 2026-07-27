"""SQLite-backed persistence for agent sessions."""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning_content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    compacted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    step INTEGER NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Persist independent agent sessions in one SQLite database."""

    def __init__(self, db_path: str | Path):
        self.connection = sqlite3.connect(str(db_path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def create_session(self, title: str = "", session_id: str | None = None) -> str:
        if session_id is None:
            session_id = uuid.uuid4().hex[:12]
        elif not _SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("session_id must match [A-Za-z0-9_-]{1,64}")

        now = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions (id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, title, now, now),
            )
        return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def session_exists(self, session_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone() is not None

    def next_turn_id(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(turn_id), 0) + 1 AS turn_id "
            "FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["turn_id"])

    def add_message(
        self,
        session_id: str,
        turn_id: int,
        role: str,
        content: str = "",
        reasoning_content: str = "",
        tool_calls: Any = None,
        tool_call_id: str | None = None,
    ) -> int:
        now = _now()
        tool_calls_json = (
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO messages (
                    session_id, turn_id, role, content, reasoning_content,
                    tool_calls, tool_call_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_id,
                    role,
                    content,
                    reasoning_content,
                    tool_calls_json,
                    tool_call_id,
                    now,
                ),
            )
            self._touch_session(session_id, now)
        return int(cursor.lastrowid)

    def context_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT role, content, reasoning_content, tool_calls, tool_call_id
            FROM messages
            WHERE session_id = ? AND compacted = 0
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        messages = []
        for row in rows:
            message: dict[str, Any] = {"role": row["role"]}
            if row["content"]:
                message["content"] = row["content"]
            if row["reasoning_content"]:
                message["reasoning_content"] = row["reasoning_content"]
            if row["tool_calls"]:
                tool_calls = json.loads(row["tool_calls"])
                if tool_calls:
                    message["tool_calls"] = tool_calls
            if row["tool_call_id"]:
                message["tool_call_id"] = row["tool_call_id"]
            messages.append(message)
        return messages

    def add_todo(self, session_id: str, text: str) -> dict[str, Any]:
        now = _now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO todos (session_id, text, created_at) VALUES (?, ?, ?)",
                (session_id, text, now),
            )
            self._touch_session(session_id, now)
        return {
            "id": int(cursor.lastrowid),
            "session_id": session_id,
            "text": text,
            "done": False,
            "created_at": now,
        }

    def list_todos(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM todos WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        return [self._todo_dict(row) for row in rows]

    def finish_todo(self, session_id: str, todo_id: int) -> bool:
        now = _now()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE todos SET done = 1 WHERE id = ? AND session_id = ?",
                (todo_id, session_id),
            )
            if cursor.rowcount:
                self._touch_session(session_id, now)
        return cursor.rowcount > 0

    def context_stats(self, session_id: str) -> tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS message_count,
                   COALESCE(SUM(LENGTH(content)), 0) AS content_chars
            FROM messages
            WHERE session_id = ? AND compacted = 0
            """,
            (session_id,),
        ).fetchone()
        return int(row["message_count"]), int(row["content_chars"])

    def compactable_messages(
        self, session_id: str, keep_user_turns: int
    ) -> list[dict[str, Any]]:
        if keep_user_turns < 0:
            raise ValueError("keep_user_turns must be non-negative")

        if keep_user_turns == 0:
            rows = self.connection.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ? AND compacted = 0
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        else:
            kept_turns = self.connection.execute(
                """
                SELECT turn_id FROM messages
                WHERE session_id = ? AND compacted = 0
                GROUP BY turn_id
                ORDER BY turn_id DESC
                LIMIT ?
                """,
                (session_id, keep_user_turns),
            ).fetchall()
            if len(kept_turns) < keep_user_turns:
                return []
            oldest_kept_turn = min(row["turn_id"] for row in kept_turns)
            rows = self.connection.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ? AND compacted = 0 AND turn_id < ?
                ORDER BY id
                """,
                (session_id, oldest_kept_turn),
            ).fetchall()
        return [self._message_dict(row) for row in rows]

    def get_summary(self, session_id: str) -> str:
        row = self.connection.execute(
            "SELECT summary FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row["summary"] if row else ""

    def save_summary_and_compact(
        self, session_id: str, summary: str, message_ids: list[int]
    ) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?",
                (summary, now, session_id),
            )
            if message_ids:
                placeholders = ", ".join("?" for _ in message_ids)
                self.connection.execute(
                    "UPDATE messages SET compacted = 1 "
                    f"WHERE session_id = ? AND id IN ({placeholders})",
                    (session_id, *message_ids),
                )

    def add_trace(
        self,
        session_id: str,
        step: int,
        event: str,
        data: Any,
        duration_ms: int = 0,
    ) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO traces (session_id, step, event, data, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    step,
                    event,
                    json.dumps(data, ensure_ascii=False),
                    duration_ms,
                    now,
                ),
            )
            self._touch_session(session_id, now)

    def list_traces(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM traces
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [self._trace_dict(row) for row in rows]

    def _touch_session(self, session_id: str, now: str) -> None:
        self.connection.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )

    @staticmethod
    def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
        message = dict(row)
        message["compacted"] = bool(message["compacted"])
        if message["tool_calls"] is not None:
            message["tool_calls"] = json.loads(message["tool_calls"])
        return message

    @staticmethod
    def _todo_dict(row: sqlite3.Row) -> dict[str, Any]:
        todo = dict(row)
        todo["done"] = bool(todo["done"])
        return todo

    @staticmethod
    def _trace_dict(row: sqlite3.Row) -> dict[str, Any]:
        trace = dict(row)
        trace["data"] = json.loads(trace["data"])
        return trace
