"""Chat de consola contra el agente real — mismo kernel, canal distinto.

    uv run python scripts/chat.py

Usa el modelo de Anthropic de verdad (**cuesta dinero**), las tools de pedidos
reales y el flujo de aprobación real: un pedido se parkea y se aprueba
escribiendo "sí". Es el `Worker` y el `TurnService` de producción con un canal
que imprime en vez de llamar a la Graph API — la demo de que el kernel no sabe
qué canal lo llama. Respeta `DATABASE_PATH` y `LANGFUSE_*` del `.env`.

Sal con "salir" o Ctrl-D.
"""

import asyncio

from agent.adapters.models.anthropic import AnthropicModelProvider
from agent.adapters.restaurante.pedidos import InMemoryPedidoStore, pedidos_tools
from agent.adapters.restaurante.policy import CustomerScoped
from agent.adapters.restaurante.sqlite import SqlitePedidoStore
from agent.adapters.stores.memory import InMemoryPendingActions, InMemorySessionStore
from agent.adapters.stores.sqlite import SqlitePendingActions, SqliteSessionStore
from agent.core.approval import StoreApprovalGate
from agent.core.context import InstructionsContext
from agent.core.memory import WindowMemory
from agent.core.observability import TracedAgentRunner, TracedModelProvider, TracedToolExecutor
from agent.core.policy import AllOf, ConfirmWrites
from agent.core.runner import ReasoningLoop
from agent.core.tools import PolicyExecutor, StaticToolRegistry
from agent.core.turns import TurnService
from agent.domain.agent import AgentSpec, InboundEvent, RunLimits
from agent.edges.channels import InboundRequest, OutboundMessage, RoutedEvent
from agent.edges.whatsapp.app import INSTRUCTIONS
from agent.edges.whatsapp.replies import YesNoReplies
from agent.edges.whatsapp.responder import TextResponder
from agent.edges.worker import StaticAgentDirectory, Worker
from agent.platform.clock import SystemClock
from agent.platform.config import get_settings
from agent.platform.tracing import NoopTracer, Tracer

AGENT_KEY = "console"
USER = "console-user"  # hace de wa_id: tenant, session y principal


class ConsoleChannel:
    """El "canal": send() imprime. parse_inbound/verify no aplican aquí."""

    def parse_inbound(self, request: InboundRequest) -> RoutedEvent | None:
        raise NotImplementedError("console input doesn't arrive by webhook")

    def verify(self, request: InboundRequest) -> str | None:
        return None

    async def send(self, message: OutboundMessage) -> None:
        print(f"\n🤖 {message.text}\n")


def build_worker() -> tuple[Worker, Tracer]:
    settings = get_settings()
    clock = SystemClock()
    db = settings.database_path

    tracer: Tracer = NoopTracer()
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        from agent.adapters.tracing.langfuse import LangfuseTracer

        tracer = LangfuseTracer.from_settings(settings)

    pedido_store = SqlitePedidoStore(db) if db else InMemoryPedidoStore()
    tools = pedidos_tools(pedido_store, clock)
    spec = AgentSpec(
        name="console-assistant",
        instructions=INSTRUCTIONS,  # las mismas del edge de WhatsApp, a propósito
        model=settings.anthropic_model,
        tool_names=[t.spec.name for t in tools],
        limits=RunLimits(max_steps=settings.agent_max_steps, max_tokens=settings.agent_max_tokens),
    )

    registry = StaticToolRegistry(tools)
    pending = SqlitePendingActions(db) if db else InMemoryPendingActions()
    sessions = SqliteSessionStore(db) if db else InMemorySessionStore()
    gate = StoreApprovalGate(pending, clock)
    executor = TracedToolExecutor(
        PolicyExecutor(registry, AllOf(CustomerScoped(), ConfirmWrites()), gate), tracer
    )
    gate.bind(executor)  # approved calls go through the traced boundary too
    loop = ReasoningLoop(
        TracedModelProvider(AnthropicModelProvider.from_settings(settings), tracer),
        InstructionsContext(clock),
        registry,
        executor,
    )
    turns = TurnService(TracedAgentRunner(loop, tracer), WindowMemory(sessions), gate)
    worker = Worker(
        StaticAgentDirectory({AGENT_KEY: spec}),
        turns,
        gate,
        YesNoReplies(),
        TextResponder(),
        ConsoleChannel(),
    )
    return worker, tracer


async def main() -> None:
    worker, tracer = build_worker()
    print("Chat local contra el agente real (modelo de verdad: cuesta dinero).")
    print('Prueba: "¿qué pedidos tengo?" · "quiero 2 tacos al pastor" · "sí" · "salir"\n')
    turn = 0
    try:
        while True:
            try:
                text = input("tú> ").strip()
            except EOFError:
                break
            if not text or text.lower() in {"salir", "exit", "quit"}:
                break
            turn += 1
            event = InboundEvent(
                event_id=f"console-{turn}",
                tenant_id=USER,
                session_id=USER,
                principal_id=USER,
                text=text,
            )
            await worker.process(RoutedEvent(agent_key=AGENT_KEY, event=event))
    finally:
        flush = getattr(tracer, "flush", None)
        if flush:
            flush()


if __name__ == "__main__":
    asyncio.run(main())
