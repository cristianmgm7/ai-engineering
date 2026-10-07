"""L3 · AnthropicModelProvider — the ``ModelProvider`` port over the Anthropic SDK.

Calling a vendor API is touching the world, so this lives in ``adapters/``; the
port and the neutral types it translates to stay in ``platform/model.py``. The only
module that imports ``anthropic``. It translates the neutral
``ModelRequest`` into ``messages.create`` params, and the SDK's ``Message`` back
into a neutral ``ModelResponse``. Nothing else: no loop, no retries of its own (the
SDK already retries 408/409/429/5xx and connection errors), no prompt logic.

Blocks the kernel doesn't model (``thinking``, ``redacted_thinking``, ...) come
back as ``OpaqueBlock`` and are replayed verbatim, because with thinking on an
assistant turn that called a tool must be sent back unchanged.
"""

from typing import Any

import anthropic
from anthropic.types import Message as SdkMessage

from agent.platform.config import Settings
from agent.platform.model import (
    ContentBlock,
    Message,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    OpaqueBlock,
    Role,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

PROVIDER = "anthropic"

_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


class AnthropicModelProvider:
    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "AnthropicModelProvider":
        api_key = settings.anthropic_api_key.get_secret_value()
        return cls(anthropic.AsyncAnthropic(api_key=api_key))

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            response = await self._client.messages.create(**to_params(request))
        except anthropic.APIStatusError as e:
            retryable = e.status_code == 429 or e.status_code >= 500
            raise ModelProviderError(str(e), retryable=retryable, status_code=e.status_code) from e
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            raise ModelProviderError(str(e), retryable=True) from e
        return from_sdk(response)


# --- Neutral → SDK ----------------------------------------------------------------


_EPHEMERAL = {"type": "ephemeral"}


def to_params(request: ModelRequest) -> dict[str, Any]:
    """Cache breakpoints (at most 3 of the 4 allowed): after each ``SystemBlock`` with
    ``cache=True``; and with ``request.cache``, after the last tool and on the
    conversation tail (top-level automatic caching), so later steps of the same run
    reuse everything before them."""
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "messages": [_message_param(m) for m in request.messages],
    }
    system = [
        {"type": "text", "text": b.text, **({"cache_control": _EPHEMERAL} if b.cache else {})}
        for b in request.system
        if b.text
    ]
    if system:
        params["system"] = system
    if request.tools:
        tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in request.tools
        ]
        if request.cache:
            tools[-1]["cache_control"] = _EPHEMERAL
        params["tools"] = tools
    if request.cache:
        params["cache_control"] = _EPHEMERAL
    return params


def _message_param(message: Message) -> dict[str, Any]:
    blocks = [p for b in message.content if (p := _block_param(b)) is not None]
    return {"role": message.role.value, "content": blocks}


def _block_param(block: ContentBlock) -> dict[str, Any] | None:
    match block:
        case TextBlock():
            return {"type": "text", "text": block.text}
        case ToolUseBlock():
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        case ToolResultBlock():
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        case OpaqueBlock(provider=p) if p == PROVIDER:
            return block.raw
        case OpaqueBlock():
            return None  # another provider's block means nothing here


# --- SDK → neutral ----------------------------------------------------------------


def from_sdk(response: SdkMessage) -> ModelResponse:
    content: list[ContentBlock] = []
    for block in response.content:
        if block.type == "text":
            content.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            content.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))
        else:
            raw = block.model_dump(mode="json", exclude_none=True)
            content.append(OpaqueBlock(provider=PROVIDER, raw=raw))

    usage = response.usage
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=content),
        stop_reason=_STOP_REASONS.get(response.stop_reason or "", StopReason.OTHER),
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        ),
        model=response.model,
    )
