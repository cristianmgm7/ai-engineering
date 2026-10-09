"""The menú connector: the tool the model sees, plus the kernel-free stores.

``menu__listar`` is a READ: ``ConfirmWrites`` lets it run without approval, and
there is no customer argument because the menu is tenant data — every customer
sees the same catalog. ``CachedMenuStore`` follows the ``Traced*`` pattern:
same port, wraps the real store and delegates; the tools never know whether a
call hit the POS or the cache.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel

from agent.core.run import RunContext
from agent.core.tools import FunctionTool, Tool
from agent.domain.agent import Effect
from agent.domain.restaurante.menu import MenuItem, MenuStore
from agent.platform.clock import Clock


class InMemoryMenuStore:
    """A fixed catalog. For tests and POS-less local runs."""

    def __init__(self, items: Iterable[MenuItem] = ()) -> None:
        self._items = list(items)

    async def list_available(self) -> list[MenuItem]:
        return list(self._items)


class CachedMenuStore:
    """Same port, a TTL cache in front: the menu is the POS's truth and changes
    rarely, so we re-read it at most once per ``ttl`` instead of per turn."""

    def __init__(self, inner: MenuStore, clock: Clock, ttl: timedelta) -> None:
        self._inner = inner
        self._clock = clock
        self._ttl = ttl
        self._cached: list[MenuItem] | None = None
        self._read_at: datetime | None = None

    async def list_available(self) -> list[MenuItem]:
        now = self._clock.now()
        if self._cached is None or self._read_at is None or now - self._read_at >= self._ttl:
            self._cached = await self._inner.list_available()
            self._read_at = now
        return list(self._cached)


class MenuListarArgs(BaseModel):
    pass  # one menu for everyone; there is nothing to choose


def menu_tools(store: MenuStore) -> list[Tool]:
    """The connector's tools, bound to one store."""

    async def listar(args: MenuListarArgs, ctx: RunContext) -> str:
        items = await store.list_available()
        if not items:
            return "El menú no está disponible en este momento."
        return "\n".join(f"{i.nombre} — {i.precio_texto()}" for i in items)

    return [
        FunctionTool(
            "menu__listar",
            "Lista el menú del restaurante con precios.",
            MenuListarArgs,
            listar,
            Effect.READ,
        ),
    ]
