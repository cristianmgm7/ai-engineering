"""POS-backed implementations of the domain ports: DTO ↔ entity translation.

The POS is the **source of truth** for the catalog and the orders; nothing here
copies them into our database. Two things only we keep, because the POS can't:

- the ``customer_id → invoice ids`` index — it doesn't know our WhatsApp
  customers; a customer only ever lists their own invoices through it;
- each order's creation time — ``GET /invoice/{id}`` returns no dates, so the
  clock-of-record for *our* listing is the moment we placed it.

Both in memory for now (they die with the process; a SQLite index is the
natural next slice).

Prices and codes are resolved here, against the catalog — the model orders by
dish name and never supplies a price (``priceUnit`` is the catalog's, always).
"""

import logging
import unicodedata
from datetime import datetime

from agent.adapters.restaurante.pos.client import (
    PosClient,
    PosClientData,
    PosError,
    PosInvoice,
    PosNewInvoice,
    PosNewItem,
    PosProduct,
)
from agent.domain.restaurante.menu import MenuItem
from agent.domain.restaurante.pedidos import (
    DatosEntrega,
    LineaPedido,
    Pedido,
    PlatosDesconocidos,
)

logger = logging.getLogger(__name__)


class PosMenuStore:
    """``MenuStore`` against the POS catalog. VectorPOS prices are plain COP
    numbers; the domain keeps money as integer cents."""

    def __init__(self, client: PosClient) -> None:
        self._client = client

    async def list_available(self) -> list[MenuItem]:
        products = await self._client.list_products()
        return [
            MenuItem(id=p.id, nombre=p.name, precio_centavos=round(p.price * 100)) for p in products
        ]


class PosPedidoStore:
    """``PedidoStore`` against the POS: ``place`` registers, ``list_for`` reads back.

    ``place`` re-reads the catalog to resolve each dish name to its code and
    price; a dish the catalog doesn't have raises ``PlatosDesconocidos`` and no
    invoice is created. ``POST /invoice`` has no idempotency key, so nothing
    here retries — a timeout means we may not know whether the order exists,
    and guessing by re-POSTing would duplicate it (learning-log material).
    """

    def __init__(self, client: PosClient) -> None:
        self._client = client
        # the index: ours alone — invoice id + when we placed it (the POS keeps no dates)
        self._orders_by_customer: dict[str, list[tuple[str, datetime]]] = {}

    async def place(
        self,
        customer_id: str,
        lineas: list[LineaPedido],
        entrega: DatosEntrega,
        creado: datetime,
    ) -> Pedido:
        by_name = {_clave(p.name): p for p in await self._client.list_products()}
        resueltas: list[tuple[LineaPedido, PosProduct]] = []
        desconocidos: list[str] = []
        for linea in lineas:
            producto = by_name.get(_clave(linea.plato))
            if producto is None:
                desconocidos.append(linea.plato)
            else:
                resueltas.append((linea, producto))
        if desconocidos:
            raise PlatosDesconocidos(desconocidos)

        draft = PosNewInvoice(
            client=PosClientData(
                name=entrega.nombre, phone=entrega.telefono, address=entrega.direccion
            ),
            items=tuple(
                PosNewItem(
                    code=producto.id,
                    amount=linea.cantidad,
                    priceUnit=producto.price,  # the catalog's price, never the model's
                    details=linea.detalles,
                )
                for linea, producto in resueltas
            ),
            delivery_details="Pedido por WhatsApp",
        )
        invoice_id = await self._client.create_invoice(draft)
        self._orders_by_customer.setdefault(customer_id, []).append((invoice_id, creado))
        items = tuple(  # display what got registered: the catalog's names, not the spelling
            _texto(linea.cantidad, producto.name, linea.detalles) for linea, producto in resueltas
        )
        return Pedido(id=invoice_id, customer_id=customer_id, items=items, creado=creado)

    async def list_for(self, customer_id: str) -> list[Pedido]:
        pedidos: list[Pedido] = []
        for invoice_id, creado in self._orders_by_customer.get(customer_id, []):
            try:
                invoice = await self._client.get_invoice(invoice_id)
            except PosError as e:  # one missing invoice shouldn't hide the rest
                logger.warning("POS invoice %s unreadable: %s", invoice_id, e)
                continue
            pedidos.append(_pedido(invoice, customer_id, creado))
        return sorted(pedidos, key=lambda p: p.creado)


def _pedido(invoice: PosInvoice, customer_id: str, creado: datetime) -> Pedido:
    """The translation: the POS's invoice becomes our entity. Lines come back
    from the POS (it registered them); ``creado`` is ours (it keeps no dates)."""
    items = tuple(_texto(item.amount, item.name, item.details) for item in invoice.items)
    return Pedido(id=invoice.id, customer_id=customer_id, items=items, creado=creado)


def _texto(cantidad: float, nombre: str, detalles: str | None) -> str:
    base = f"{cantidad:g}× {nombre}"
    return f"{base} ({detalles})" if detalles else base


def _clave(nombre: str) -> str:
    """Catalog lookup key: case-, spacing- and accent-insensitive, so the
    model's "hamburguesa clasica" finds the POS's "HAMBURGUESA CLÁSICA"."""
    plano = unicodedata.normalize("NFKD", nombre)
    sin_acentos = "".join(c for c in plano if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())
