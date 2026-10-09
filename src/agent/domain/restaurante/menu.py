"""Menú — the restaurant's catalog, read from the external POS.

Tenant-level data, not customer data: every customer sees the same menu, so
nothing here carries a ``customer_id`` and ``CustomerScoped`` has no say. The
POS is the source of truth; we never persist the menu, at most cache it
(``CachedMenuStore`` in adapters). The store is a port; the POS-backed
implementation lives in ``adapters/restaurante/pos/``.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict


class MenuItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # the POS's product id (sku)
    nombre: str
    precio_centavos: int  # money as integer cents; floats drift

    def precio_texto(self) -> str:
        return f"${self.precio_centavos / 100:.2f}"


class MenuStore(Protocol):
    """What the business needs from a menu source — not what the POS offers."""

    async def list_available(self) -> list[MenuItem]: ...
