"""L3 · SQLite stores — state that survives a restart (aiosqlite).

Rows hold the Pydantic JSON (``model_dump_json`` in, ``model_validate_json``
out), with just enough columns pulled out to index on. Each operation opens a
short-lived connection: simple and correct for one process; a pool arrives
when it hurts. ``claim`` is the real atomic find-and-delete the port demands
(``DELETE … RETURNING``), unlike the in-memory store's single-threaded luck.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from agent.domain.agent import Message, PendingAction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_session ON messages (session_id, seq);
CREATE INDEX IF NOT EXISTS messages_by_event ON messages (session_id, event_id);

CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_by_session ON pending_actions (session_id, principal_id);
"""


@asynccontextmanager
async def open_db(path: str, schema: str = _SCHEMA) -> AsyncIterator[aiosqlite.Connection]:
    """One connection with the schema ensured. Product stores pass their own schema."""
    db = await aiosqlite.connect(path)
    try:
        await db.executescript(schema)
        yield db
    finally:
        await db.close()


class SqliteSessionStore:
    """Every message of every session, append-only, keyed by session id."""

    def __init__(self, path: str) -> None:
        self._path = path

    async def load(self, session_id: str) -> list[Message]:
        async with open_db(self._path) as db:
            cursor = await db.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)
            )
            rows = await cursor.fetchall()
        return [Message.model_validate_json(payload) for (payload,) in rows]

    async def append(self, session_id: str, messages: list[Message]) -> None:
        async with open_db(self._path) as db:
            await db.executemany(
                "INSERT INTO messages (session_id, event_id, payload) VALUES (?, ?, ?)",
                [(session_id, m.event_id, m.model_dump_json()) for m in messages],
            )
            await db.commit()

    async def has_event(self, session_id: str, event_id: str) -> bool:
        async with open_db(self._path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND event_id = ? LIMIT 1",
                (session_id, event_id),
            )
            return await cursor.fetchone() is not None


class SqlitePendingActionStore:
    """Parked approvals that outlive the process — the point of this station."""

    def __init__(self, path: str) -> None:
        self._path = path

    async def add(self, action: PendingAction) -> None:
        async with open_db(self._path) as db:
            await db.execute(
                "INSERT INTO pending_actions (id, session_id, principal_id, payload) "
                "VALUES (?, ?, ?, ?)",
                (action.id, action.session_id, action.principal_id, action.model_dump_json()),
            )
            await db.commit()

    async def get(self, action_id: str) -> PendingAction | None:
        async with open_db(self._path) as db:
            cursor = await db.execute(
                "SELECT payload FROM pending_actions WHERE id = ?", (action_id,)
            )
            row = await cursor.fetchone()
        return PendingAction.model_validate_json(row[0]) if row else None

    async def claim(self, action_id: str) -> PendingAction | None:
        async with open_db(self._path) as db:
            cursor = await db.execute(
                "DELETE FROM pending_actions WHERE id = ? RETURNING payload", (action_id,)
            )
            row = await cursor.fetchone()
            await db.commit()
        return PendingAction.model_validate_json(row[0]) if row else None

    async def for_session(self, session_id: str, principal_id: str) -> list[PendingAction]:
        async with open_db(self._path) as db:
            cursor = await db.execute(
                "SELECT payload FROM pending_actions WHERE session_id = ? AND principal_id = ?",
                (session_id, principal_id),
            )
            rows = await cursor.fetchall()
        return [PendingAction.model_validate_json(payload) for (payload,) in rows]
