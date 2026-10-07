"""L0 · Model access — the provider-neutral vocabulary and the ``ModelProvider`` port.

Everything the agent says to or hears from an LLM is expressed in these types. A
vendor SDK (Anthropic today) lives behind one ``ModelProvider`` implementation in
``adapters/models/`` and translates to and from them, so no other layer imports a
vendor SDK.

``Message`` and its content blocks live here, not in ``domain/``, because they are
the model's wire format: L0 is the innermost layer and ``ModelRequest`` needs them.
The domain imports them, never the other way round.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# --- Content blocks -------------------------------------------------------------


class TextBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """The model asking to call a tool. Also serves as the standard ``ToolCall``."""

    model_config = ConfigDict(frozen=True)

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    """What we send back for one ``ToolUseBlock``, matched by ``tool_use_id``."""

    model_config = ConfigDict(frozen=True)

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


class OpaqueBlock(BaseModel):
    """A provider block the kernel doesn't interpret, kept verbatim (e.g. ``thinking``).

    Some blocks must be sent back to the provider exactly as received: with thinking
    on, an assistant turn that called a tool has to be replayed unchanged on the next
    request. Wrapping them keeps ``Message`` provider-neutral without losing them.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["opaque"] = "opaque"
    provider: str  # which provider produced it; others must drop it
    raw: dict[str, Any]


ContentBlock = Annotated[
    TextBlock | ToolUseBlock | ToolResultBlock | OpaqueBlock, Field(discriminator="type")
]


class Message(BaseModel):
    """One entry in a conversation: a role plus a list of content blocks.

    Frozen: history is append-only, so a message must never mutate.
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    content: list[ContentBlock]
    event_id: str | None = None  # the InboundEvent it came from, if any (idempotency)
    created_at: datetime = Field(default_factory=_utcnow)

    @classmethod
    def text(cls, role: Role, text: str, event_id: str | None = None) -> "Message":
        return cls(role=role, content=[TextBlock(text=text)], event_id=event_id)

    def plain_text(self) -> str:
        """Concatenate the text blocks; tool blocks are not audible text."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]


# --- Request / response ---------------------------------------------------------


class ToolDefinition(BaseModel):
    """What the model sees of a tool: name, description and a JSON Schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"  # declined by the provider's safety classifiers
    OTHER = "other"  # pause, stop sequence... anything else the loop treats as "stop"


class Usage(BaseModel):
    """Tokens for one model call: the raw material for cost."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class SystemBlock(BaseModel):
    """One part of the system prompt. ``cache=True`` puts a cache breakpoint after it:
    everything up to here (tools, then system up to this block) can be reused."""

    model_config = ConfigDict(frozen=True)

    text: str
    cache: bool = False


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    system: list[SystemBlock] = Field(default_factory=list)  # a plain str is accepted too
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    max_tokens: int = 16000  # output cap; with thinking on, reasoning counts against it
    cache: bool = False  # also cache the tool list and the conversation so far

    @field_validator("system", mode="before")
    @classmethod
    def _plain_text_system(cls, value: object) -> object:
        if isinstance(value, str):
            return [SystemBlock(text=value)] if value else []
        return value

    def system_text(self) -> str:
        return "\n\n".join(b.text for b in self.system)


class ModelResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: Message  # always role=assistant
    stop_reason: StopReason
    usage: Usage
    model: str  # the model that actually answered


class ModelProviderError(Exception):
    """A model call failed after the provider's own retries.

    Provider implementations translate their SDK's exceptions into this, so the core
    can handle failures without importing a vendor SDK.
    """

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ModelProvider(Protocol):
    """The only door to an LLM."""

    async def generate(self, request: ModelRequest) -> ModelResponse: ...
