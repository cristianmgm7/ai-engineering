"""Tests for adapters/whatsapp/pedidos.py — the connector, sender-scoped by construction."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent.adapters.whatsapp.pedidos import InMemoryPedidoStore, pedidos_tools
from agent.core.run import RunContext
from agent.domain.agent import AgentSpec
from agent.platform.clock import FixedClock

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
CLIENTE_A = "5215550001111"
CLIENTE_B = "5215550002222"


def ctx(principal: str = CLIENTE_A) -> RunContext:
    spec = AgentSpec(
        name="t", instructions="i", model="m", tool_names=["pedidos__listar", "pedidos__crear"]
    )
    return RunContext(spec=spec, tenant_id=principal, session_id=principal, principal_id=principal)


def tools(store: InMemoryPedidoStore):
    by_name = {t.spec.name: t for t in pedidos_tools(store, FixedClock(NOW))}
    return by_name["pedidos__listar"], by_name["pedidos__crear"]


async def test_placing_and_listing_work_for_the_sender():
    listar, crear = tools(InMemoryPedidoStore())
    empty = await listar.run(listar.args_model(), ctx())
    assert "Sin pedidos" in empty.content

    args = crear.args_model(items=["2 tacos al pastor", "1 agua de horchata"])
    placed = await crear.run(args, ctx())
    assert not placed.is_error and "pedido-1" in placed.content

    mine = await listar.run(listar.args_model(), ctx())
    assert "2 tacos al pastor" in mine.content and "2026-10-06 14:00" in mine.content


async def test_an_empty_order_cannot_be_placed():
    _, crear = tools(InMemoryPedidoStore())
    with pytest.raises(ValidationError):  # no items at all: rejected at the schema
        crear.args_model(items=[])
    blank = await crear.run(crear.args_model(items=["   "]), ctx())  # only blanks: domain rule
    assert blank.is_error and "al menos un plato" in blank.content


async def test_a_customer_never_sees_another_customers_pedidos():
    store = InMemoryPedidoStore()
    await store.place(CLIENTE_B, ["1 pozole"], NOW)
    listar, crear = tools(store)

    await crear.run(crear.args_model(items=["2 tacos al pastor"]), ctx(CLIENTE_A))

    mine = await listar.run(listar.args_model(), ctx(CLIENTE_A))
    assert "tacos" in mine.content and "pozole" not in mine.content
    theirs = await listar.run(listar.args_model(), ctx(CLIENTE_B))
    assert "pozole" in theirs.content and "tacos" not in theirs.content
