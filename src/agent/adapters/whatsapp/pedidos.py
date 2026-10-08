"""The pedidos connector: tools + the in-memory store (SQLite arrives next station).

The tools are sender-scoped **by construction**: every store call is keyed by
``ctx.principal_id`` and no argument lets the model pick another customer.
``CustomerScoped`` (policy.py) is the defense in depth on top. ``crear`` is a
WRITE, so ``ConfirmWrites`` parks it and the customer approves in the chat.
"""

from datetime import datetime
from itertools import count

from pydantic import BaseModel, Field

from agent.core.run import RunContext
from agent.core.tools import FunctionTool, Tool, ToolResult
from agent.domain.agent import Effect
from agent.domain.whatsapp.pedidos import Pedido, PedidoStore, rechazo_para
from agent.platform.clock import Clock


class InMemoryPedidoStore:
    def __init__(self) -> None:
        self._by_customer: dict[str, list[Pedido]] = {}
        self._ids = count(1)

    async def list_for(self, customer_id: str) -> list[Pedido]:
        return sorted(self._by_customer.get(customer_id, []), key=lambda p: p.creado)

    async def place(self, customer_id: str, items: list[str], creado: datetime) -> Pedido:
        pedido = Pedido(
            id=f"pedido-{next(self._ids)}",
            customer_id=customer_id,
            items=tuple(items),
            creado=creado,
        )
        self._by_customer.setdefault(customer_id, []).append(pedido)
        return pedido


class ListarArgs(BaseModel):
    pass  # only the sender's own orders exist; there is nothing to choose


class CrearArgs(BaseModel):
    items: list[str] = Field(
        min_length=1,  # pydantic rejects an empty order; the domain rule re-checks blanks
        description="Los platos del pedido, en las palabras del cliente, uno por entrada "
        '(p. ej. ["2 tacos al pastor", "1 agua de horchata"]).',
    )


def pedidos_tools(store: PedidoStore, clock: Clock) -> list[Tool]:
    """The connector's tools, bound to one store and one clock."""

    async def listar(args: ListarArgs, ctx: RunContext) -> str:
        pedidos = await store.list_for(ctx.principal_id)
        if not pedidos:
            return "Sin pedidos."
        return "\n".join(
            f"{p.id} ({p.creado:%Y-%m-%d %H:%M}): {', '.join(p.items)}" for p in pedidos
        )

    async def crear(args: CrearArgs, ctx: RunContext) -> ToolResult | str:
        rechazo = rechazo_para(args.items)
        if rechazo:
            return ToolResult.error(f"No se puede crear el pedido: {rechazo}.")
        pedido = await store.place(ctx.principal_id, args.items, clock.now())
        return f"Pedido {pedido.id} creado: {', '.join(pedido.items)}."

    return [
        FunctionTool(
            "pedidos__listar",
            "Lista los pedidos del cliente que escribe.",
            ListarArgs,
            listar,
            Effect.READ,
        ),
        FunctionTool(
            "pedidos__crear",
            "Crea un pedido para el cliente que escribe. El sistema pide su aprobación.",
            CrearArgs,
            crear,
            Effect.WRITE,
        ),
    ]
