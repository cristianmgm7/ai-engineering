"""Pedidos (orders) — the bot's first business domain: a restaurant.

Deterministic rules plus the store port. The entity lives in ``models.py`` and
is re-exported here. ``customer_id`` is the owner's channel id (on WhatsApp, the
``wa_id``): the tools never let the model choose it, and ``CustomerScoped``
denies any attempt. The store is a port; implementations live in ``adapters/``.
"""

from datetime import datetime
from typing import Protocol

from agent.domain.restaurante.models import Pedido

__all__ = ["Pedido", "PedidoStore", "rechazo_para"]


class PedidoStore(Protocol):
    async def list_for(self, customer_id: str) -> list[Pedido]: ...

    async def place(self, customer_id: str, items: list[str], creado: datetime) -> Pedido: ...


def rechazo_para(items: list[str]) -> str | None:
    """Deterministic ordering rule: why this order can't be placed, or ``None``.

    Business rules live here, in code the model can't argue with. (The menu,
    prices and totals belong here too, when a catalog exists.)
    """
    if not [item for item in items if item.strip()]:
        return "un pedido necesita al menos un plato"
    return None
