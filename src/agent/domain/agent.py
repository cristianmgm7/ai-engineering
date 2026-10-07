"""L1 · Agent entities — the nouns every agent project has (reference architecture §3).

Project-specific concepts are mapped onto these at the edge. For a WhatsApp bot:
the chat is the ``session_id``, the customer's ``wa_id`` is the ``principal_id``,
and the tenant is the sender (sender-scoped). ``Message`` and the content blocks
are re-exported from ``platform.model`` because they are the model's wire format.

Product entities (e.g. Connector, ConnectorAccount) will live in a product
subpackage (``domain/whatsapp/``) when a component needs them.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agent.platform.model import (
    ContentBlock,
    Message,
    Role,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "AgentSpec",
    "ContentBlock",
    "Effect",
    "InboundEvent",
    "Message",
    "PendingAction",
    "Role",
    "RunLimits",
    "TextBlock",
    "ToolResultBlock",
    "ToolSpec",
    "ToolUseBlock",
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InboundEvent(BaseModel):
    """A normalized input. Every ChannelAdapter produces this, whatever the channel."""

    model_config = ConfigDict(frozen=True)

    event_id: str  # idempotency key from the channel (WhatsApp: the wamid)
    tenant_id: str  # whose data and config (the sender: sender-scoped)
    session_id: str  # the conversation thread (WhatsApp: the 1:1 chat)
    principal_id: str  # who is acting (WhatsApp: the customer's wa_id)
    text: str


class RunLimits(BaseModel):
    """Hard stops the harness enforces, whatever the model wants."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=8, ge=1)  # model calls per run
    max_tokens: int = Field(default=16000, ge=1)  # output tokens per call, thinking included


class AgentSpec(BaseModel):
    """An agent as data: adding one should be config, not a new class."""

    model_config = ConfigDict(frozen=True)

    name: str
    instructions: str
    model: str
    tool_names: list[str] = Field(default_factory=list)
    limits: RunLimits = Field(default_factory=RunLimits)


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolSpec(ToolDefinition):
    """A tool's model-facing definition plus what it does to the world.

    ``effect`` never reaches the model. Policy uses it to decide whether a call
    needs approval.
    """

    effect: Effect = Effect.READ


class PendingAction(BaseModel):
    """A tool call parked by the ApprovalGate, waiting for a human decision."""

    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    principal_id: str
    call: ToolUseBlock
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None  # after this, a decision is refused
