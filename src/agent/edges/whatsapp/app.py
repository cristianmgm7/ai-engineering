"""Composition root: wires the kernel with the Anthropic model and WhatsApp.

    uv run uvicorn agent.edges.whatsapp.app:create_app --factory --reload

With ``DATABASE_PATH`` set, sessions, parked approvals and pedidos live in
SQLite and survive a restart; unset, everything is in memory (the queue always
is). Tools: the pedidos connector, under ``AllOf(CustomerScoped(),
ConfirmWrites())`` — a customer only touches their own pedidos, and placing
one needs their yes in the chat.
"""

from dataclasses import dataclass

import httpx
from fastapi import FastAPI

from agent.adapters.models.anthropic import AnthropicModelProvider
from agent.adapters.stores.memory import InMemoryPendingActions, InMemorySessionStore
from agent.adapters.stores.sqlite import SqlitePendingActions, SqliteSessionStore
from agent.adapters.whatsapp.pedidos import InMemoryPedidoStore, pedidos_tools
from agent.adapters.whatsapp.policy import CustomerScoped
from agent.adapters.whatsapp.sqlite import SqlitePedidoStore
from agent.core.approval import StoreApprovalGate
from agent.core.context import InstructionsContext
from agent.core.memory import WindowMemory
from agent.core.policy import AllOf, ConfirmWrites
from agent.core.runner import ReasoningLoop
from agent.core.tools import PolicyExecutor, StaticToolRegistry, Tool
from agent.core.turns import TurnService
from agent.domain.agent import AgentSpec, RunLimits
from agent.edges.ingress import ChannelRoute, create_ingress
from agent.edges.whatsapp.adapter import WhatsAppAdapter
from agent.edges.whatsapp.replies import YesNoReplies
from agent.edges.whatsapp.responder import TextResponder
from agent.edges.worker import InProcessQueue, StaticAgentDirectory, Worker
from agent.platform.clock import Clock, SystemClock
from agent.platform.config import Settings, get_settings
from agent.platform.model import ModelProvider

INSTRUCTIONS = """\
You are a business assistant on WhatsApp. Customers write to you in a chat.

- Keep replies short: one to four sentences of plain text. Use WhatsApp
  emphasis (*bold*, _italics_) sparingly; no headers, no lists unless asked.
- Reply in the customer's language.
- Never invent facts about the business or the customer's data. If you can't
  do something with the tools you have, say so plainly.
- When you create or change something, just call the tool. The system asks the
  customer for approval; never ask for confirmation yourself."""


@dataclass
class WhatsAppApp:
    http_app: FastAPI
    queue: InProcessQueue


def build(
    settings: Settings,
    *,
    model: ModelProvider | None = None,
    http: httpx.AsyncClient | None = None,
    clock: Clock | None = None,
    tools: list[Tool] | None = None,
) -> WhatsAppApp:
    clock = clock or SystemClock()
    db = settings.database_path
    if tools is None:  # the product's default connector; pass [] for a bare agent
        pedido_store = SqlitePedidoStore(db) if db else InMemoryPedidoStore()
        tools = pedidos_tools(pedido_store, clock)
    agents: dict[str, AgentSpec] = {}
    if settings.whatsapp_phone_number_id:
        agents[settings.whatsapp_phone_number_id] = AgentSpec(
            name="whatsapp-assistant",
            instructions=INSTRUCTIONS,
            model=settings.anthropic_model,
            tool_names=[t.spec.name for t in tools],
            limits=RunLimits(
                max_steps=settings.agent_max_steps, max_tokens=settings.agent_max_tokens
            ),
        )
    directory = StaticAgentDirectory(agents)

    registry = StaticToolRegistry(tools)
    pending = SqlitePendingActions(db) if db else InMemoryPendingActions()
    sessions = SqliteSessionStore(db) if db else InMemorySessionStore()
    gate = StoreApprovalGate(pending, clock)
    executor = PolicyExecutor(registry, AllOf(CustomerScoped(), ConfirmWrites()), gate)
    gate.bind(executor)
    loop = ReasoningLoop(
        model or AnthropicModelProvider.from_settings(settings),
        InstructionsContext(clock),
        registry,
        executor,
    )
    turns = TurnService(loop, WindowMemory(sessions), gate)

    verify_token = settings.whatsapp_verify_token
    app_secret = settings.whatsapp_app_secret
    access_token = settings.whatsapp_access_token
    adapter = WhatsAppAdapter(
        verify_token=verify_token.get_secret_value() if verify_token else None,
        app_secret=app_secret.get_secret_value() if app_secret else None,
        http=http or httpx.AsyncClient(timeout=20.0),
        access_token=access_token.get_secret_value() if access_token else None,
        graph_version=settings.whatsapp_graph_version,
    )
    worker = Worker(directory, turns, gate, YesNoReplies(), TextResponder(), adapter)
    queue = InProcessQueue(worker)
    http_app = create_ingress({"whatsapp": ChannelRoute(adapter, queue)}, directory)
    return WhatsAppApp(http_app=http_app, queue=queue)


def create_app() -> FastAPI:
    """The uvicorn factory."""
    return build(get_settings()).http_app
