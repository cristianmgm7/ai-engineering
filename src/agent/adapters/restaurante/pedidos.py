"""The pedidos connector: tools + the in-memory store.

The tools are sender-scoped **by construction**: every store call is keyed by
``ctx.principal_id`` and no argument lets the model pick another customer.
``CustomerScoped`` (policy.py) is the defense in depth on top. ``crear`` is a
WRITE, so ``ConfirmWrites`` parks it and the customer approves in the chat.

Prices never appear in the arguments: the model orders by dish name and the
store resolves codes and prices against the catalog (``PosPedidoStore``), or
just records the lines verbatim (the local stores, which have no catalog).
"""

from datetime import datetime
from itertools import count

from pydantic import BaseModel, Field

from agent.core.run import RunContext
from agent.core.tools import FunctionTool, Tool, ToolResult
from agent.domain.agent import Effect
from agent.domain.restaurante.pedidos import (
    DatosEntrega,
    LineaPedido,
    Pedido,
    PedidoStore,
    PlatosDesconocidos,
    rechazo_para,
)
from agent.platform.clock import Clock


class InMemoryPedidoStore:
    def __init__(self) -> None:
        self._by_customer: dict[str, list[Pedido]] = {}
        self._ids = count(1)

    async def list_for(self, customer_id: str) -> list[Pedido]:
        return sorted(self._by_customer.get(customer_id, []), key=lambda p: p.creado)

    async def place(
        self,
        customer_id: str,
        lineas: list[LineaPedido],
        entrega: DatosEntrega,
        creado: datetime,
    ) -> Pedido:
        pedido = Pedido(
            id=f"pedido-{next(self._ids)}",
            customer_id=customer_id,
            items=tuple(linea.texto() for linea in lineas),
            creado=creado,
        )
        self._by_customer.setdefault(customer_id, []).append(pedido)
        return pedido


class ListarArgs(BaseModel):
    pass  # only the sender's own orders exist; there is nothing to choose


class LineaArgs(BaseModel):
    plato: str = Field(
        description="El plato, con el nombre exacto como aparece en el menú (menu__listar)."
    )
    cantidad: int = Field(default=1, ge=1, description="Cuántas unidades de este plato.")
    detalles: str | None = Field(
        default=None, description='Ajustes del cliente para este plato (p. ej. "sin cebolla").'
    )


class CrearArgs(BaseModel):
    lineas: list[LineaArgs] = Field(
        min_length=1,  # pydantic rejects an empty order; the domain rule re-checks blanks
        description="Las líneas del pedido, una por plato.",
    )
    nombre: str = Field(
        description="Nombre de quien recibe el pedido. Pregúntaselo al cliente si no lo sabes."
    )
    direccion: str = Field(
        description='Dirección de entrega, o "recoge en el local" si pasa por él.'
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
        lineas = [
            LineaPedido(plato=li.plato, cantidad=li.cantidad, detalles=li.detalles)
            for li in args.lineas
        ]
        rechazo = rechazo_para(lineas)
        if rechazo:
            return ToolResult.error(f"No se puede crear el pedido: {rechazo}.")
        entrega = DatosEntrega(
            nombre=args.nombre,
            telefono=ctx.principal_id,  # the sender's own number; the model never picks it
            direccion=args.direccion,
        )
        try:
            pedido = await store.place(ctx.principal_id, lineas, entrega, clock.now())
        except PlatosDesconocidos as e:
            return ToolResult.error(
                f"No están en el menú: {', '.join(e.platos)}. "
                "Consulta menu__listar y usa los nombres exactos."
            )
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
            "Crea un pedido para el cliente que escribe. Necesita su nombre y una dirección "
            "de entrega. El sistema pide su aprobación.",
            CrearArgs,
            crear,
            Effect.WRITE,
        ),
    ]
