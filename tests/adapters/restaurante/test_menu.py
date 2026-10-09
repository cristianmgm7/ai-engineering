"""Tests for adapters/restaurante/menu.py — the menu tool and the TTL cache."""

from datetime import UTC, datetime, timedelta

from agent.adapters.restaurante.menu import (
    CachedMenuStore,
    InMemoryMenuStore,
    MenuListarArgs,
    menu_tools,
)
from agent.core.run import RunContext
from agent.domain.agent import AgentSpec, Effect
from agent.domain.restaurante.menu import MenuItem
from agent.platform.clock import FixedClock

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
CTX = RunContext(
    spec=AgentSpec(name="a", instructions="", model="m", tool_names=["menu__listar"]),
    tenant_id="wa-1",
    session_id="s1",
    principal_id="wa-1",
)


def item(nombre: str, centavos: int = 2550) -> MenuItem:
    return MenuItem(id=nombre[:3].upper(), nombre=nombre, precio_centavos=centavos)


class CountingStore:
    def __init__(self, items: list[MenuItem]) -> None:
        self.items, self.reads = items, 0

    async def list_available(self) -> list[MenuItem]:
        self.reads += 1
        return list(self.items)


async def test_listar_is_a_read_and_formats_prices():
    (tool,) = menu_tools(InMemoryMenuStore([item("Taco al pastor")]))
    assert tool.spec.effect is Effect.READ  # ConfirmWrites lets it run unapproved
    result = await tool.run(MenuListarArgs(), CTX)
    assert result.content == "Taco al pastor — $25.50"


async def test_an_empty_menu_says_so_instead_of_inventing():
    (tool,) = menu_tools(InMemoryMenuStore())
    result = await tool.run(MenuListarArgs(), CTX)
    assert "no está disponible" in result.content


async def test_cache_reads_the_inner_store_once_until_the_ttl_passes():
    inner = CountingStore([item("Taco")])
    clock = FixedClock(NOW)
    store = CachedMenuStore(inner, clock, ttl=timedelta(minutes=5))

    await store.list_available()
    await store.list_available()
    assert inner.reads == 1  # second call was the cache

    clock._at = NOW + timedelta(minutes=6)  # time passes; the cache expired
    await store.list_available()
    assert inner.reads == 2
