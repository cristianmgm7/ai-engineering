"""Tests for adapters/whatsapp/sqlite.py — pedidos that survive a restart."""

from datetime import UTC, datetime

from agent.adapters.whatsapp.sqlite import SqlitePedidoStore

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
CLIENTE_A = "5215550001111"
CLIENTE_B = "5215550002222"


def db(tmp_path) -> str:
    return str(tmp_path / "agent.db")


async def test_pedidos_survive_a_restart_and_stay_isolated(tmp_path):
    store = SqlitePedidoStore(db(tmp_path))
    placed = await store.place(CLIENTE_A, ["2 tacos al pastor", "1 agua"], NOW)
    await store.place(CLIENTE_B, ["1 pozole"], NOW)
    assert placed.id == "pedido-1" and placed.items == ("2 tacos al pastor", "1 agua")

    restarted = SqlitePedidoStore(db(tmp_path))
    mine = await restarted.list_for(CLIENTE_A)
    assert [p.id for p in mine] == ["pedido-1"]
    assert mine[0].creado == NOW and "pozole" not in str(mine[0].items)
    assert [p.items for p in await restarted.list_for(CLIENTE_B)] == [("1 pozole",)]
