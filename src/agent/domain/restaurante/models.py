"""The business's entities, in one file (like a mobile ``entities/`` folder).

Pure data: pydantic, frozen, no behavior beyond formatting helpers. Ports and
rules stay in each concept's file (``pedidos.py``, ``menu.py``), which re-export
their entities so both import paths work — the same move ``domain/agent.py``
makes with ``platform.model`` types.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LineaPedido(BaseModel):
    """One line of an order, structured: the POS registers codes and quantities,
    not prose. ``plato`` is the dish name — the menu's name when a catalog
    exists; prices are never here, they come from the catalog alone."""

    model_config = ConfigDict(frozen=True)

    plato: str
    cantidad: int = Field(default=1, ge=1)
    detalles: str | None = None  # the customer's tweaks ("sin cebolla")

    def texto(self) -> str:
        base = f"{self.cantidad}× {self.plato}"
        return f"{base} ({self.detalles})" if self.detalles else base


class DatosEntrega(BaseModel):
    """Who receives the order and where. The POS requires name, phone and
    address on every order; ``telefono`` is the sender's channel id (wa_id),
    set by the tool — never by the model."""

    model_config = ConfigDict(frozen=True)

    nombre: str
    telefono: str
    direccion: str  # or "recoge en el local"


class Pedido(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    customer_id: str  # the wa_id that owns it
    items: tuple[str, ...]  # display lines ("2× COMBO HAMBURGUESA (sin cebolla)")
    creado: datetime  # timezone-aware


class MenuItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # the POS's product id (sku)
    nombre: str
    precio_centavos: int  # money as integer cents; floats drift

    def precio_texto(self) -> str:
        return f"${self.precio_centavos / 100:.2f}"
