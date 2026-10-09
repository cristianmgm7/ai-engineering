"""Composition root: the ONE place that knows the concrete classes and wires them.

Mirrors `edges/whatsapp/app.py::build`. In Dart this would be your get_it /
injectable setup; here it is just a function that calls constructors.
"""

import asyncio

from aprendizaje.adaptadores import InMemoryReminderStore, PrintNotifier, SystemClock
from aprendizaje.servicio import ReminderService


def build() -> ReminderService:
    return ReminderService(InMemoryReminderStore(), PrintNotifier(), SystemClock())


async def main() -> None:
    service = build()
    await service.schedule("ana", "call the supplier", in_minutes=0)
    await service.deliver_due("ana")


if __name__ == "__main__":
    asyncio.run(main())  # run with: uv run python -m aprendizaje.composicion
