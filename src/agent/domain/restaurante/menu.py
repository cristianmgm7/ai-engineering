"""Menú — the restaurant's catalog, read from the external POS.

Tenant-level data, not customer data: every customer sees the same menu, so
nothing here carries a ``customer_id`` and ``CustomerScoped`` has no say. The
POS is the source of truth; we never persist the menu, at most cache it
(``CachedMenuStore`` in adapters). The entity lives in ``models.py`` and is
re-exported here; the POS-backed store lives in ``adapters/restaurante/pos/``.
"""

from typing import Protocol

from agent.domain.restaurante.models import MenuItem

__all__ = ["MenuItem", "MenuStore"]


class MenuStore(Protocol):
    """What the business needs from a menu source — not what the POS offers."""

    async def list_available(self) -> list[MenuItem]: ...
