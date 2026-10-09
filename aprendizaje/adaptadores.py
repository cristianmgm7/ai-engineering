"""Real implementations of the ports. Mirrors `adapters/stores/memory.py` + `platform/clock.py`.

Note: nothing here says `implements Clock`. It is enough that the methods match.
"""

from datetime import UTC, datetime

from aprendizaje.modelos import Reminder


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class InMemoryReminderStore:
    def __init__(self) -> None:
        self._by_owner: dict[str, list[Reminder]] = {}

    async def add(self, reminder: Reminder) -> None:
        self._by_owner.setdefault(reminder.owner_id, []).append(reminder)

    async def pending_for(self, owner_id: str) -> list[Reminder]:
        return list(self._by_owner.get(owner_id, []))  # a copy: callers can't mutate our state


class PrintNotifier:
    async def send(self, owner_id: str, text: str) -> None:
        print(f"[to {owner_id}] {text}")
