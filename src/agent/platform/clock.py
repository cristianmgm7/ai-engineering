"""L0 · Clock — an injected "now", so prompts and tests are deterministic.

Without today's date in the prompt the model invents one, and
a test that reads the real clock can't assert on it. So nothing reads
``datetime.now()`` directly: it asks a ``Clock``.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at one instant. For tests and evals."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at
