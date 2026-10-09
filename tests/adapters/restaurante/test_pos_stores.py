"""Tests for adapters/restaurante/pos/stores.py — DTO→entity translation and the
customer→orders index. The fake plays PosClient structurally (duck typing)."""

from datetime import UTC, datetime

from agent.adapters.restaurante.pos.client import PosError, PosOrder, PosProduct
from agent.adapters.restaurante.pos.stores import PosMenuStore, PosPedidoStore

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class FakePosClient:
    """Orders live in a dict, like the real POS's database would."""

    def __init__(self, products: list[PosProduct] = ()) -> None:
        self.products = list(products)
        self.orders: dict[str, PosOrder] = {}
        self.idempotency_keys: list[str] = []

    async def list_products(self) -> list[PosProduct]:
        return list(self.products)

    async def create_order(self, items: list[str], idempotency_key: str) -> PosOrder:
        self.idempotency_keys.append(idempotency_key)
        order = PosOrder(
            id=f"ord-{len(self.orders) + 1}", items=tuple(items), status="received", created_at=NOW
        )
        self.orders[order.id] = order
        return order

    async def get_order(self, order_id: str) -> PosOrder:
        if order_id not in self.orders:
            raise PosError(404, "order not found")
        return self.orders[order_id]


def product(sku: str, available: bool = True) -> PosProduct:
    return PosProduct(sku=sku, name=f"Plato {sku}", price_cents=1000, available=available)


async def test_menu_translates_dtos_and_drops_unavailable_products():
    store = PosMenuStore(FakePosClient(products=[product("A"), product("B", available=False)]))
    items = await store.list_available()
    assert [i.id for i in items] == ["A"]
    assert items[0].precio_texto() == "$10.00"


async def test_place_registers_in_the_pos_and_returns_our_entity():
    pos = FakePosClient()
    store = PosPedidoStore(pos)
    pedido = await store.place("wa-1", ["2 tacos"], creado=NOW)
    assert pedido.id in pos.orders  # the POS holds the truth
    assert pedido.customer_id == "wa-1"  # the POS never knew the wa_id; we attach it
    assert pos.idempotency_keys[0]  # every POST carries a key


async def test_list_for_only_returns_the_senders_orders():
    """The isolation negative: the index is per customer, like everything else."""
    store = PosPedidoStore(FakePosClient())
    await store.place("ana", ["pedido de ana"], creado=NOW)
    await store.place("bob", ["pedido de bob"], creado=NOW)
    assert [p.items for p in await store.list_for("ana")] == [("pedido de ana",)]
    assert await store.list_for("nadie") == []


async def test_an_unreadable_order_is_skipped_not_fatal():
    pos = FakePosClient()
    store = PosPedidoStore(pos)
    kept = await store.place("ana", ["se queda"], creado=NOW)
    lost = await store.place("ana", ["se pierde"], creado=NOW)
    del pos.orders[lost.id]  # the POS forgot it (or purged it)
    assert [p.id for p in await store.list_for("ana")] == [kept.id]
