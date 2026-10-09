"""Domain models + ports (the "abstractions") for the mini example.

Mirrors the repo's L1 (`domain/agent.py`) and L0 (`platform/clock.py`) files.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


# DECISION 1: a model is a pydantic BaseModel with frozen=True, like `Pedido` and
# `InboundEvent` in the repo. Frozen = immutable (like a Dart `final` class / a TS
# `readonly` object). Pydantic also validates and coerces data at construction.
# Use `@dataclass` instead only for internal types that need no validation
# (the repo does that in `evaluation/`).
class Reminder(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    owner_id: str  # who owns it: every query is scoped by this (sender isolation)
    text: str
    due_at: datetime  # timezone-aware


# DECISION 2: ports are `typing.Protocol`, not ABC. A class satisfies a Protocol just
# by having the same methods (structural typing, like TS interfaces). It does NOT need
# to write `implements`/`extends`. So the domain declares what it needs, and the
# implementations don't even import this file.
class Clock(Protocol):
    def now(self) -> datetime: ...


class ReminderStore(Protocol):
    async def add(self, reminder: Reminder) -> None: ...

    async def pending_for(self, owner_id: str) -> list[Reminder]: ...


class Notifier(Protocol):
    async def send(self, owner_id: str, text: str) -> None: ...
