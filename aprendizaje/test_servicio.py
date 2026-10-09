"""Tests: no mock library, only hand-written fakes (the repo style, see tests/core/test_runner.py).

Run (from the repo root; the repo's pytest config uses importlib mode, so override it):
    uv run pytest aprendizaje --import-mode=prepend
"""

from datetime import UTC, datetime, timedelta

import pytest

from aprendizaje.adaptadores import InMemoryReminderStore
from aprendizaje.servicio import MAX_PENDING, ReminderService, TooManyReminders

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# DECISION 6: fakes are tiny classes that satisfy the Protocol structurally. We don't
# use unittest.mock/MagicMock: a fake with a `.sent` list is easier to read and fails
# loudly when the real interface changes. (The repo has zero uses of MagicMock.)
class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, owner_id: str, text: str) -> None:
        self.sent.append((owner_id, text))


# DECISION 7: a pytest fixture is the "test constructor". Each test gets a fresh set,
# and the fakes are returned too so the test can inspect/mutate them.
@pytest.fixture
def parts():
    clock, notifier = FixedClock(NOW), RecordingNotifier()
    service = ReminderService(InMemoryReminderStore(), notifier, clock)
    return service, clock, notifier


# DECISION 8: `asyncio_mode = "auto"` (in pyproject) lets us write plain `async def test_...`.
async def test_schedule_sets_due_time_from_the_injected_clock(parts):
    service, _, _ = parts
    reminder = await service.schedule("ana", "call", in_minutes=30)
    assert reminder.due_at == NOW + timedelta(minutes=30)


async def test_only_due_reminders_are_delivered(parts):
    service, clock, notifier = parts
    await service.schedule("ana", "soon", in_minutes=5)
    await service.schedule("ana", "later", in_minutes=60)
    clock.at = NOW + timedelta(minutes=10)  # we "travel in time" thanks to the injected clock
    assert await service.deliver_due("ana") == 1
    assert notifier.sent == [("ana", "soon")]


# Negative tests, like the repo's isolation tests: the rule is only as good as what it refuses.
async def test_limit_is_per_owner_not_global(parts):
    service, _, _ = parts
    for _ in range(MAX_PENDING):
        await service.schedule("ana", "x", in_minutes=1)
    with pytest.raises(TooManyReminders):
        await service.schedule("ana", "one too many", in_minutes=1)
    await service.schedule("bob", "bob is unaffected", in_minutes=1)  # must not raise


async def test_one_owner_never_gets_anothers_reminders(parts):
    service, clock, notifier = parts
    await service.schedule("ana", "secret of ana", in_minutes=0)
    assert await service.deliver_due("bob") == 0
    assert notifier.sent == []
