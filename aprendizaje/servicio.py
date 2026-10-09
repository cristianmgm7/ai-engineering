"""The service: business logic that depends only on ports (never on implementations).

Mirrors `core/turns.py::TurnService` and `core/tools.py::PolicyExecutor`.
"""

from datetime import timedelta
from itertools import count

from aprendizaje.modelos import Clock, Notifier, Reminder, ReminderStore

MAX_PENDING = 3


class TooManyReminders(Exception):
    """A business rule refused the request. Domain errors are exceptions, like `SendFailed`."""


class ReminderService:
    # DECISION 3: constructor injection, exactly like Dart. No DI container, no
    # decorator: whoever builds the service passes its collaborators. The parameters
    # are typed with the PORTS, so the service can't tell Sqlite from in-memory.
    def __init__(self, store: ReminderStore, notifier: Notifier, clock: Clock) -> None:
        # DECISION 4: a leading underscore = "private by convention". Python has no
        # `private`; the underscore is an agreement between programmers.
        self._store = store
        self._notifier = notifier
        self._clock = clock
        # DECISION 5: a local counter instead of uuid, so ids are deterministic in tests.
        self._ids = count(1)

    async def schedule(self, owner_id: str, text: str, in_minutes: int) -> Reminder:
        # Isolation: we only ever look at THIS owner's reminders.
        if len(await self._store.pending_for(owner_id)) >= MAX_PENDING:
            raise TooManyReminders(f"max {MAX_PENDING} pending reminders")
        reminder = Reminder(
            id=f"rem-{next(self._ids)}",
            owner_id=owner_id,
            text=text,
            due_at=self._clock.now() + timedelta(minutes=in_minutes),
        )
        await self._store.add(reminder)
        return reminder

    async def deliver_due(self, owner_id: str) -> int:
        """Send every reminder of this owner that is already due; return how many."""
        now = self._clock.now()
        due = [r for r in await self._store.pending_for(owner_id) if r.due_at <= now]
        for reminder in due:
            await self._notifier.send(reminder.owner_id, reminder.text)
        return len(due)
