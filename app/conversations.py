"""Persistent chat sessions: saving, resuming and deleting conversations.

A session is an ordered list of turns — alternating questions and answers —
stored so that closing the browser does not lose the work. Accreditation
questions are rarely one-shot: an answer usually raises a follow-up, and the
follow-up only makes sense against what came before.

Conversations live in their own SQLite file (``conversations.db``) rather than
alongside the document index. Rebuilding the index is a routine operation, and
chat history must never be collateral damage of an ``ingest --rebuild``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import SETTINGS, Settings
from .safety import SOURCE_GUARD, SourceTreeGuard

LOGGER = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    """One question or answer within a session."""

    role: str
    content: str
    #: Citation records, present only on assistant turns.
    citations: list[dict] = field(default_factory=list)
    backend_name: str = ""
    model_name: str = ""
    elapsed_seconds: float = 0.0
    created_at: str = ""
    turn_id: int = 0

    @property
    def is_question(self) -> bool:
        """True when this turn is the user's question."""
        return self.role == "user"

    def as_dictionary(self) -> dict:
        """Represent the turn as JSON-serialisable data for the interface."""
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "content": self.content,
            "citations": self.citations,
            "backend_name": self.backend_name,
            "model_name": self.model_name,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "created_at": self.created_at,
        }


@dataclass
class ConversationSession:
    """A named conversation, optionally with its turns loaded."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    turn_count: int = 0
    turns: list[ChatTurn] = field(default_factory=list)

    def as_dictionary(self, include_turns: bool = False) -> dict:
        """Represent the session as JSON-serialisable data."""
        payload = {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
        }
        if include_turns:
            payload["turns"] = [turn.as_dictionary() for turn in self.turns]
        return payload


class SessionTitleBuilder:
    """Derives a short, recognisable title from the first question asked.

    Sessions are far easier to find in a list by their opening question than by
    a timestamp, so the title is generated rather than demanded from the user.
    """

    MAXIMUM_LENGTH = 60
    DEFAULT_TITLE = "Percakapan baru"

    def build(self, question: str) -> str:
        """Return a trimmed one-line title for ``question``."""
        collapsed = " ".join(question.split())
        if not collapsed:
            return self.DEFAULT_TITLE
        if len(collapsed) <= self.MAXIMUM_LENGTH:
            return collapsed
        truncated = collapsed[: self.MAXIMUM_LENGTH]
        last_space = truncated.rfind(" ")
        # Cut at a word boundary when one is reasonably close to the limit.
        if last_space > self.MAXIMUM_LENGTH * 0.6:
            truncated = truncated[:last_space]
        return truncated + "…"


class ConversationStore:
    """SQLite-backed storage for chat sessions and their turns.

    Shares one connection across threads under a lock, matching the pattern used
    by :class:`app.storage.DocumentCatalogue`; the write volume here is a couple
    of rows per question, so contention is irrelevant.
    """

    _SCHEMA_STATEMENTS = (
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS turns (
            turn_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            citations_json  TEXT,
            backend_name    TEXT,
            model_name      TEXT,
            elapsed_seconds REAL,
            created_at      TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS index_turns_by_session ON turns(session_id)",
    )

    def __init__(self, settings: Settings, guard: SourceTreeGuard) -> None:
        """Open (creating if needed) the conversation database."""
        self._settings = settings
        database_path: Path = guard.assert_writable(
            settings.data_directory / "conversations.db"
        )
        self._access_lock = threading.RLock()
        self._title_builder = SessionTitleBuilder()
        self._connection = sqlite3.connect(
            database_path, check_same_thread=False, timeout=30.0
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        """Create the session and turn tables if this is a fresh database."""
        with self._access_lock:
            for statement in self._SCHEMA_STATEMENTS:
                self._connection.execute(statement)
            self._connection.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        with self._access_lock:
            self._connection.close()

    def _now(self) -> str:
        """Current local time as an ISO string, to the second."""
        return datetime.now().isoformat(timespec="seconds")

    # --- Sessions -----------------------------------------------------------

    def create_session(self, title: str | None = None) -> ConversationSession:
        """Start a new, empty session and return it."""
        session_id = uuid.uuid4().hex[:16]
        timestamp = self._now()
        resolved_title = (title or SessionTitleBuilder.DEFAULT_TITLE).strip()

        with self._access_lock:
            self._connection.execute(
                "INSERT INTO sessions (session_id, title, created_at, updated_at) "
                "VALUES (?,?,?,?)",
                (session_id, resolved_title, timestamp, timestamp),
            )
            self._connection.commit()
        return ConversationSession(
            session_id=session_id,
            title=resolved_title,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def session_exists(self, session_id: str) -> bool:
        """True when a session with this identifier is stored."""
        with self._access_lock:
            row = self._connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row is not None

    def list_sessions(self, limit: int = 100) -> list[ConversationSession]:
        """Return sessions, most recently used first."""
        with self._access_lock:
            rows = self._connection.execute(
                """
                SELECT s.session_id, s.title, s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.session_id)
                           AS turn_count
                FROM sessions s
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ConversationSession(
                session_id=row["session_id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                turn_count=int(row["turn_count"]),
            )
            for row in rows
        ]

    def load_session(self, session_id: str) -> ConversationSession | None:
        """Return one session with all of its turns, or None when unknown."""
        with self._access_lock:
            row = self._connection.execute(
                "SELECT session_id, title, created_at, updated_at FROM sessions "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            turn_rows = self._connection.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_id",
                (session_id,),
            ).fetchall()

        turns = [self._row_to_turn(turn_row) for turn_row in turn_rows]
        return ConversationSession(
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turn_count=len(turns),
            turns=turns,
        )

    def rename_session(self, session_id: str, title: str) -> bool:
        """Give a session a new title; returns False when it does not exist."""
        cleaned = " ".join(title.split())[: SessionTitleBuilder.MAXIMUM_LENGTH * 2]
        if not cleaned:
            return False
        with self._access_lock:
            cursor = self._connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (cleaned, self._now(), session_id),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and every turn in it."""
        with self._access_lock:
            self._connection.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def delete_all_sessions(self) -> int:
        """Delete every session, returning how many were removed."""
        with self._access_lock:
            count = self._connection.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            self._connection.execute("DELETE FROM turns")
            self._connection.execute("DELETE FROM sessions")
            self._connection.commit()
        return int(count)

    # --- Turns --------------------------------------------------------------

    def append_turn(self, session_id: str, turn: ChatTurn) -> int:
        """Store one turn and bump the session's last-used time.

        Returns the new turn's identifier, or 0 when the session is unknown —
        a stale browser tab holding a deleted session must not create orphans.
        """
        if not self.session_exists(session_id):
            LOGGER.warning("Sesi %s tidak ditemukan, giliran diabaikan", session_id)
            return 0

        timestamp = turn.created_at or self._now()
        with self._access_lock:
            cursor = self._connection.execute(
                """
                INSERT INTO turns (session_id, role, content, citations_json,
                                   backend_name, model_name, elapsed_seconds, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    turn.role,
                    turn.content,
                    json.dumps(turn.citations, ensure_ascii=False) if turn.citations else None,
                    turn.backend_name,
                    turn.model_name,
                    turn.elapsed_seconds,
                    timestamp,
                ),
            )
            self._connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (timestamp, session_id),
            )
            self._connection.commit()
        return int(cursor.lastrowid)

    def recent_turns(self, session_id: str, limit: int) -> list[ChatTurn]:
        """Return the last ``limit`` turns of a session, oldest first.

        Used to give the model the conversation so far. The query fetches the
        newest rows and reverses them, so a long session costs the same as a
        short one.
        """
        if limit <= 0:
            return []
        with self._access_lock:
            rows = self._connection.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._row_to_turn(row) for row in reversed(rows)]

    def ensure_titled(self, session_id: str, first_question: str) -> None:
        """Replace the placeholder title once a real question has been asked."""
        with self._access_lock:
            row = self._connection.execute(
                "SELECT title FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None or row["title"] != SessionTitleBuilder.DEFAULT_TITLE:
            return
        self.rename_session(session_id, self._title_builder.build(first_question))

    def _row_to_turn(self, row: sqlite3.Row) -> ChatTurn:
        """Map a stored row onto a turn object."""
        citations: list[dict] = []
        if row["citations_json"]:
            try:
                citations = json.loads(row["citations_json"])
            except json.JSONDecodeError:
                LOGGER.warning("Sitasi tersimpan tidak dapat dibaca pada giliran %s", row["turn_id"])
        return ChatTurn(
            turn_id=int(row["turn_id"]),
            role=row["role"],
            content=row["content"],
            citations=citations,
            backend_name=row["backend_name"] or "",
            model_name=row["model_name"] or "",
            elapsed_seconds=float(row["elapsed_seconds"] or 0.0),
            created_at=row["created_at"],
        )

    def collect_statistics(self) -> dict:
        """Summarise stored conversations for the status panel."""
        with self._access_lock:
            row = self._connection.execute(
                "SELECT (SELECT COUNT(*) FROM sessions) AS session_count, "
                "       (SELECT COUNT(*) FROM turns) AS turn_count, "
                "       (SELECT MAX(updated_at) FROM sessions) AS last_used"
            ).fetchone()
        return {
            "session_count": int(row["session_count"] or 0),
            "turn_count": int(row["turn_count"] or 0),
            "last_used": row["last_used"],
        }


#: Process-wide conversation store.
CONVERSATION_STORE = ConversationStore(SETTINGS, SOURCE_GUARD)
