"""The business's entities, in one file (like a mobile ``entities/`` folder).

Pure data: pydantic, frozen, no behavior beyond formatting helpers. Ports and
rules stay in each concept's file (``pedidos.py``, ``menu.py``), which re-export
their entities so both import paths work — the same move ``domain/agent.py``
makes with ``platform.model`` types.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Pedido(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    customer_id: str  # the wa_id that owns it
    items: tuple[str, ...]  # the dishes in the customer's words ("2 tacos al pastor")
    creado: datetime  # timezone-aware


class MenuItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # the POS's product id (sku)
    nombre: str
    precio_centavos: int  # money as integer cents; floats drift

    def precio_texto(self) -> str:
        return f"${self.precio_centavos / 100:.2f}"
