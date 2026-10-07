"""The pedidos store on SQLite — orders survive a restart.

Shares the database file with the kernel stores (its own table), reusing
``open_db`` with a product schema. The row id doubles as the pedido id.
"""

import json
from datetime import datetime

from agent.adapters.stores.sqlite import open_db
from agent.domain.whatsapp.pedidos import Pedido

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    items TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pedidos_by_customer ON pedidos (customer_id, id);
"""


class SqlitePedidoStore:
    def __init__(self, path: str) -> None:
        self._path = path

    async def list_for(self, customer_id: str) -> list[Pedido]:
        async with open_db(self._path, _SCHEMA) as db:
            cursor = await db.execute(
                "SELECT id, items, creado FROM pedidos WHERE customer_id = ? ORDER BY id",
                (customer_id,),
            )
            rows = await cursor.fetchall()
        return [_pedido(row_id, customer_id, items, creado) for row_id, items, creado in rows]

    async def place(self, customer_id: str, items: list[str], creado: datetime) -> Pedido:
        async with open_db(self._path, _SCHEMA) as db:
            cursor = await db.execute(
                "INSERT INTO pedidos (customer_id, items, creado) VALUES (?, ?, ?)",
                (customer_id, json.dumps(items), creado.isoformat()),
            )
            await db.commit()
            row_id = cursor.lastrowid
        assert row_id is not None
        return _pedido(row_id, customer_id, json.dumps(items), creado.isoformat())


def _pedido(row_id: int, customer_id: str, items: str, creado: str) -> Pedido:
    return Pedido(
        id=f"pedido-{row_id}",
        customer_id=customer_id,
        items=tuple(json.loads(items)),
        creado=datetime.fromisoformat(creado),
    )
