"""Tests for adapters/models/anthropic.py, against a fake SDK client (no network)."""

from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message as SdkMessage

from agent.adapters.models.anthropic import AnthropicModelProvider, to_params
from agent.platform.model import (
    Message,
    ModelProviderError,
    ModelRequest,
    OpaqueBlock,
    Role,
    StopReason,
    SystemBlock,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


def sdk_message(content: list[dict], stop_reason: str = "end_turn", **usage: int) -> SdkMessage:
    return SdkMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, **usage},
        }
    )


class FakeMessages:
    def __init__(self, response: SdkMessage | None = None, error: Exception | None = None):
        self.response, self.error = response, error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **params: Any) -> SdkMessage:
        self.calls.append(params)
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


class FakeClient:
    def __init__(self, messages: FakeMessages):
        self.messages = messages


def provider(messages: FakeMessages) -> AnthropicModelProvider:
    return AnthropicModelProvider(FakeClient(messages))  # type: ignore[arg-type]


def request(**overrides: Any) -> ModelRequest:
    fields: dict[str, Any] = {
        "model": "claude-sonnet-5",
        "messages": [Message.text(Role.USER, "hola")],
    }
    return ModelRequest(**(fields | overrides))


# --- request mapping --------------------------------------------------------------


def test_minimal_request_omits_empty_system_and_tools():
    params = to_params(request())
    assert params == {
        "model": "claude-sonnet-5",
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hola"}]}],
    }


def test_tools_and_system_are_sent():
    schema = {"type": "object", "properties": {}}
    params = to_params(
        request(
            system="Be brief.",
            tools=[ToolDefinition(name="t", description="d", input_schema=schema)],
        )
    )
    assert params["system"] == [{"type": "text", "text": "Be brief."}]
    assert "cache_control" not in params  # caching is opt-in
    assert params["tools"] == [{"name": "t", "description": "d", "input_schema": schema}]


def test_tool_use_and_tool_result_blocks_are_mapped():
    history = [
        Message.text(Role.USER, "what's on today?"),
        Message(role=Role.ASSISTANT, content=[ToolUseBlock(id="tu1", name="cal", input={"d": 1})]),
        Message(role=Role.USER, content=[ToolResultBlock(tool_use_id="tu1", content="nothing")]),
    ]
    params = to_params(request(messages=history))
    assert params["messages"][1]["content"] == [
        {"type": "tool_use", "id": "tu1", "name": "cal", "input": {"d": 1}}
    ]
    assert params["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "nothing", "is_error": False}
    ]


def test_opaque_blocks_replay_verbatim_and_foreign_ones_are_dropped():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            OpaqueBlock(provider="anthropic", raw=thinking),
            OpaqueBlock(provider="other", raw={"type": "x"}),
            ToolUseBlock(id="tu1", name="cal", input={}),
        ],
    )
    params = to_params(request(messages=[Message.text(Role.USER, "hi"), msg]))
    assert params["messages"][1]["content"][0] == thinking
    assert len(params["messages"][1]["content"]) == 2


def test_cache_breakpoints_go_after_the_stable_system_block_the_last_tool_and_the_tail():
    schema = {"type": "object"}
    params = to_params(
        request(
            system=[SystemBlock(text="stable", cache=True), SystemBlock(text="Today is Tuesday.")],
            tools=[
                ToolDefinition(name="a", description="", input_schema=schema),
                ToolDefinition(name="b", description="", input_schema=schema),
            ],
            cache=True,
        )
    )
    assert params["system"] == [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Today is Tuesday."},
    ]
    assert "cache_control" not in params["tools"][0]
    assert params["tools"][1]["cache_control"] == {"type": "ephemeral"}
    assert params["cache_control"] == {"type": "ephemeral"}


def test_empty_system_blocks_are_not_sent():
    params = to_params(request(system=[SystemBlock(text="", cache=True)]))
    assert "system" not in params


# --- response mapping -------------------------------------------------------------


async def test_text_response_is_mapped():
    fake = FakeMessages(sdk_message([{"type": "text", "text": "Hola!", "citations": None}]))
    response = await provider(fake).generate(request())
    assert response.message.role is Role.ASSISTANT
    assert response.message.plain_text() == "Hola!"
    assert response.stop_reason is StopReason.END_TURN
    assert response.model == "claude-sonnet-5"
    assert fake.calls[0]["model"] == "claude-sonnet-5"


async def test_tool_use_response_keeps_thinking_as_opaque():
    fake = FakeMessages(
        sdk_message(
            [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "tool_use", "id": "tu1", "name": "cal", "input": {"day": "today"}},
            ],
            stop_reason="tool_use",
        )
    )
    response = await provider(fake).generate(request())
    thinking, tool_use = response.message.content
    assert isinstance(thinking, OpaqueBlock)
    assert thinking.raw == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert tool_use == ToolUseBlock(id="tu1", name="cal", input={"day": "today"})
    assert response.stop_reason is StopReason.TOOL_USE


async def test_round_trip_replays_assistant_turn_unchanged():
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "tu1", "name": "cal", "input": {"day": "today"}},
    ]
    response = await provider(FakeMessages(sdk_message(content, "tool_use"))).generate(request())
    replayed = to_params(request(messages=[Message.text(Role.USER, "x"), response.message]))
    assert replayed["messages"][1]["content"] == content


@pytest.mark.parametrize(
    ("sdk", "neutral"),
    [
        ("max_tokens", StopReason.MAX_TOKENS),
        ("refusal", StopReason.REFUSAL),
        ("pause_turn", StopReason.OTHER),
        ("stop_sequence", StopReason.OTHER),
    ],
)
async def test_stop_reasons_are_mapped(sdk: str, neutral: StopReason):
    fake = FakeMessages(sdk_message([{"type": "text", "text": "x"}], stop_reason=sdk))
    assert (await provider(fake).generate(request())).stop_reason is neutral


async def test_usage_includes_cache_tokens_and_defaults_missing_to_zero():
    with_cache = sdk_message(
        [{"type": "text", "text": "x"}], cache_read_input_tokens=7, cache_creation_input_tokens=3
    )
    response = await provider(FakeMessages(with_cache)).generate(request())
    assert response.usage == Usage(
        input_tokens=10, output_tokens=5, cache_read_tokens=7, cache_write_tokens=3
    )
    plain = await provider(FakeMessages(sdk_message([{"type": "text", "text": "x"}]))).generate(
        request()
    )
    assert plain.usage == Usage(input_tokens=10, output_tokens=5)


# --- errors -----------------------------------------------------------------------


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=req), body=None)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (_status_error(anthropic.RateLimitError, 429), True),
        (_status_error(anthropic.InternalServerError, 500), True),
        (_status_error(anthropic.BadRequestError, 400), False),
    ],
)
async def test_status_errors_become_model_provider_errors(error: Exception, retryable: bool):
    with pytest.raises(ModelProviderError) as caught:
        await provider(FakeMessages(error=error)).generate(request())
    assert caught.value.retryable is retryable
    assert caught.value.__cause__ is error


async def test_connection_errors_are_retryable():
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    with pytest.raises(ModelProviderError) as caught:
        await provider(FakeMessages(error=anthropic.APIConnectionError(request=req))).generate(
            request()
        )
    assert caught.value.retryable is True


def test_text_block_has_no_provider_coupling():
    assert TextBlock(text="x").model_dump() == {"type": "text", "text": "x"}
