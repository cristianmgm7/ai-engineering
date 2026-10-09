"""POS-backed implementations of the domain ports: DTO ↔ entity translation.

The POS is the **source of truth** for the catalog and the orders; nothing here
copies them into our database. The one thing we do keep is the
``customer_id → order ids`` index, because the POS doesn't know our WhatsApp
customers — that mapping is data only we have. In-memory for now (it dies with
the process; a SQLite index is the natural next slice).
"""

import logging
from datetime import datetime
from uuid import uuid4

from agent.adapters.restaurante.pos.client import PosClient, PosError
from agent.domain.restaurante.menu import MenuItem
from agent.domain.restaurante.pedidos import Pedido

logger = logging.getLogger(__name__)


class PosMenuStore:
    """``MenuStore`` against the POS catalog."""

    def __init__(self, client: PosClient) -> None:
        self._client = client

    async def list_available(self) -> list[MenuItem]:
        products = await self._client.list_products()
        return [
            MenuItem(id=p.sku, nombre=p.name, precio_centavos=p.price_cents)
            for p in products
            if p.available
        ]


class PosPedidoStore:
    """``PedidoStore`` against the POS: ``place`` registers, ``list_for`` reads back.

    Open question (learning-log): the idempotency key is minted per ``place``
    call, so it only protects against retries *inside* the POS/HTTP layer. A
    retry-safe key should derive from the inbound event (the wamid), which the
    port doesn't carry today — changing the port is a conscious decision for
    when the real POS exists.
    """

    def __init__(self, client: PosClient) -> None:
        self._client = client
        self._orders_by_customer: dict[str, list[str]] = {}  # the index: ours alone

    async def place(self, customer_id: str, items: list[str], creado: datetime) -> Pedido:
        order = await self._client.create_order(items, idempotency_key=uuid4().hex)
        self._orders_by_customer.setdefault(customer_id, []).append(order.id)
        return _pedido(order.id, customer_id, order.items, order.created_at)

    async def list_for(self, customer_id: str) -> list[Pedido]:
        pedidos: list[Pedido] = []
        for order_id in self._orders_by_customer.get(customer_id, []):
            try:
                order = await self._client.get_order(order_id)
            except PosError as e:  # one missing order shouldn't hide the rest
                logger.warning("POS order %s unreadable: %s", order_id, e)
                continue
            pedidos.append(_pedido(order.id, customer_id, order.items, order.created_at))
        return sorted(pedidos, key=lambda p: p.creado)


def _pedido(order_id: str, customer_id: str, items: tuple[str, ...], creado: datetime) -> Pedido:
    """The translation: the POS's order becomes our entity. ``creado`` is the
    POS's timestamp — it registered the order, so its clock is the truth."""
    return Pedido(id=order_id, customer_id=customer_id, items=items, creado=creado)
