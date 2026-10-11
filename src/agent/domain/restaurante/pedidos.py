"""Pedidos (orders) — the bot's first business domain: a restaurant.

Deterministic rules plus the store port. The entities live in ``models.py`` and
are re-exported here. ``customer_id`` is the owner's channel id (on WhatsApp,
the ``wa_id``): the tools never let the model choose it, and ``CustomerScoped``
denies any attempt. The store is a port; implementations live in ``adapters/``.

Lines are structured (``LineaPedido``) because the real POS registers product
codes and quantities, not prose — and prices come from the catalog, never from
the model. A store backed by a catalog raises ``PlatosDesconocidos`` for dishes
it can't resolve, so the tool can tell the model to re-check the menu.
"""

from datetime import datetime
from typing import Protocol

from agent.domain.restaurante.models import DatosEntrega, LineaPedido, Pedido

__all__ = [
    "DatosEntrega",
    "LineaPedido",
    "Pedido",
    "PedidoStore",
    "PlatosDesconocidos",
    "rechazo_para",
]


class PlatosDesconocidos(Exception):
    """The catalog doesn't have these dishes; the order was not placed."""

    def __init__(self, platos: list[str]) -> None:
        super().__init__(f"platos fuera del menú: {', '.join(platos)}")
        self.platos = platos


class PedidoStore(Protocol):
    async def list_for(self, customer_id: str) -> list[Pedido]: ...

    async def place(
        self,
        customer_id: str,
        lineas: list[LineaPedido],
        entrega: DatosEntrega,
        creado: datetime,
    ) -> Pedido: ...


def rechazo_para(lineas: list[LineaPedido]) -> str | None:
    """Deterministic ordering rule: why this order can't be placed, or ``None``.

    Business rules live here, in code the model can't argue with. (Quantities
    are already bounded by the entity: ``cantidad >= 1``.)
    """
    if not [linea for linea in lineas if linea.plato.strip()]:
        return "un pedido necesita al menos un plato"
    return None
