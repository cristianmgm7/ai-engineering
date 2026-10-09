"""The external POS: a vendor the business talks to (CLAUDE.md: its port lives in
``domain/restaurante/``, its clients here). ``client.py`` speaks the POS's HTTP
API; ``stores.py`` satisfies the domain ports (``MenuStore``, ``PedidoStore``)
by translating DTOs into entities."""

from agent.adapters.restaurante.pos.client import PosClient, PosError
from agent.adapters.restaurante.pos.stores import PosMenuStore, PosPedidoStore

__all__ = ["PosClient", "PosError", "PosMenuStore", "PosPedidoStore"]
