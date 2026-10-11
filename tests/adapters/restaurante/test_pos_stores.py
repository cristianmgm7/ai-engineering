"""Tests for adapters/restaurante/pos/stores.py — DTO→entity translation, catalog
resolution (codes and prices come from the catalog, never from the caller) and
the customer→invoices index. The fake plays PosClient structurally (duck typing).
"""

from datetime import UTC, datetime

import pytest

from agent.adapters.restaurante.pos.client import (
    PosError,
    PosInvoice,
    PosInvoiceItem,
    PosNewInvoice,
    PosProduct,
)
from agent.adapters.restaurante.pos.stores import PosMenuStore, PosPedidoStore
from agent.domain.restaurante.pedidos import DatosEntrega, LineaPedido, PlatosDesconocidos

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

CATALOGO = [
    PosProduct(id="42110093", name="HAMBURGUESA CLÁSICA", price=12500),
    PosProduct(id="11910174", name="GASEOSA", price=3000),
]

ENTREGA = DatosEntrega(nombre="Ana", telefono="573001234567", direccion="Calle 1 # 2-3")


def linea(plato: str, cantidad: int = 1, detalles: str | None = None) -> LineaPedido:
    return LineaPedido(plato=plato, cantidad=cantidad, detalles=detalles)


class FakePosClient:
    """Invoices live in a dict, like the real POS's database would."""

    def __init__(self, products: list[PosProduct] = ()) -> None:
        self.products = list(products)
        self.invoices: dict[str, PosInvoice] = {}
        self.drafts: list[PosNewInvoice] = []

    async def list_products(self) -> list[PosProduct]:
        return list(self.products)

    async def create_invoice(self, draft: PosNewInvoice) -> str:
        self.drafts.append(draft)
        by_code = {p.id: p for p in self.products}
        invoice = PosInvoice(
            id=str(2000 + len(self.invoices)),
            items=tuple(
                PosInvoiceItem(
                    name=by_code[i.code].name, code=i.code, amount=i.amount, details=i.details
                )
                for i in draft.items
            ),
        )
        self.invoices[invoice.id] = invoice
        return invoice.id

    async def get_invoice(self, invoice_id: str) -> PosInvoice:
        if invoice_id not in self.invoices:
            raise PosError(404, "Cuenta no encontrada")
        return self.invoices[invoice_id]


async def test_menu_translates_pos_prices_into_integer_cents():
    store = PosMenuStore(FakePosClient(products=CATALOGO))
    items = await store.list_available()
    assert [i.id for i in items] == ["42110093", "11910174"]
    assert items[0].precio_centavos == 1250000  # COP 12500 as cents
    assert items[0].nombre == "HAMBURGUESA CLÁSICA"


async def test_place_resolves_names_against_the_catalog_and_registers():
    pos = FakePosClient(products=CATALOGO)
    store = PosPedidoStore(pos)
    pedido = await store.place(
        "wa-1", [linea("hamburguesa clasica", 2, "sin cebolla")], ENTREGA, creado=NOW
    )
    assert pedido.id in pos.invoices  # the POS holds the truth
    assert pedido.customer_id == "wa-1"  # the POS never knew the wa_id; we attach it
    assert pedido.items == ("2× HAMBURGUESA CLÁSICA (sin cebolla)",)  # the catalog's name

    sent = pos.drafts[0].items[0]
    assert sent.code == "42110093"
    assert sent.priceUnit == 12500  # the catalog's price — the caller never set one
    assert pos.drafts[0].client.phone == "573001234567"


async def test_an_unknown_dish_places_nothing_and_names_the_offender():
    pos = FakePosClient(products=CATALOGO)
    store = PosPedidoStore(pos)
    with pytest.raises(PlatosDesconocidos) as e:
        await store.place("wa-1", [linea("GASEOSA"), linea("pizza hawaiana")], ENTREGA, creado=NOW)
    assert e.value.platos == ["pizza hawaiana"]
    assert pos.drafts == []  # nothing reached the POS


async def test_list_for_only_returns_the_senders_invoices():
    """The isolation negative: the index is per customer, like everything else."""
    store = PosPedidoStore(FakePosClient(products=CATALOGO))
    await store.place("ana", [linea("GASEOSA")], ENTREGA, creado=NOW)
    await store.place("bob", [linea("HAMBURGUESA CLÁSICA")], ENTREGA, creado=NOW)
    assert [p.items for p in await store.list_for("ana")] == [("1× GASEOSA",)]
    assert await store.list_for("nadie") == []


async def test_an_unreadable_invoice_is_skipped_not_fatal():
    pos = FakePosClient(products=CATALOGO)
    store = PosPedidoStore(pos)
    kept = await store.place("ana", [linea("GASEOSA")], ENTREGA, creado=NOW)
    lost = await store.place("ana", [linea("GASEOSA")], ENTREGA, creado=NOW)
    del pos.invoices[lost.id]  # the POS forgot it (or purged it)
    assert [p.id for p in await store.list_for("ana")] == [kept.id]
