"""L3 · In-memory stores. For tests, evals and local runs: state dies with the process.

Single-process only: ``claim`` is atomic here because asyncio runs one coroutine at
a time between awaits, and the method has no ``await``. A database store needs a
real atomic find-and-delete.
"""

from agent.domain.agent import Message, PendingAction


class InMemoryPendingActions:
    def __init__(self) -> None:
        self._actions: dict[str, PendingAction] = {}

    async def add(self, action: PendingAction) -> None:
        self._actions[action.id] = action

    async def get(self, action_id: str) -> PendingAction | None:
        return self._actions.get(action_id)

    async def claim(self, action_id: str) -> PendingAction | None:
        return self._actions.pop(action_id, None)

    async def for_session(self, session_id: str, principal_id: str) -> list[PendingAction]:
        return [
            a
            for a in self._actions.values()
            if a.session_id == session_id and a.principal_id == principal_id
        ]


class InMemorySessionStore:
    """Every message of every session, append-only, keyed by session id."""

    def __init__(self) -> None:
        self._sessions: dict[str, list[Message]] = {}

    async def load(self, session_id: str) -> list[Message]:
        return list(self._sessions.get(session_id, []))

    async def append(self, session_id: str, messages: list[Message]) -> None:
        self._sessions.setdefault(session_id, []).extend(messages)

    async def has_event(self, session_id: str, event_id: str) -> bool:
        return any(m.event_id == event_id for m in self._sessions.get(session_id, []))
