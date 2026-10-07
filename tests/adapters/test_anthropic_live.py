"""Live checks against the real Anthropic API. Opt-in: ``uv run pytest -m live``.

They cost a few cents each, so the default run excludes them (see pyproject
``addopts``). Needs ``ANTHROPIC_API_KEY`` in ``.env``.
"""

import pytest

from agent.adapters.models.anthropic import AnthropicModelProvider
from agent.platform.config import get_settings
from agent.platform.model import (
    Message,
    ModelRequest,
    Role,
    StopReason,
    ToolDefinition,
    ToolResultBlock,
)

pytestmark = pytest.mark.live


@pytest.fixture
def setup():
    get_settings.cache_clear()
    settings = get_settings()
    return AnthropicModelProvider.from_settings(settings), settings.anthropic_model


async def test_one_real_call_returns_text_and_usage(setup):
    provider, model = setup
    response = await provider.generate(
        ModelRequest(
            model=model,
            system="Answer in one short sentence.",
            messages=[Message.text(Role.USER, "What is the capital of Colombia?")],
        )
    )
    assert response.stop_reason is StopReason.END_TURN
    assert "Bogot" in response.message.plain_text()
    assert response.usage.input_tokens > 0 and response.usage.output_tokens > 0


async def test_tool_round_trip_replays_the_assistant_turn(setup):
    provider, model = setup
    tool = ToolDefinition(
        name="get_weather",
        description="Current weather for a city.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    history = [Message.text(Role.USER, "Use the tool: what's the weather in Medellín?")]
    first = await provider.generate(ModelRequest(model=model, messages=history, tools=[tool]))
    assert first.stop_reason is StopReason.TOOL_USE
    call = first.message.tool_uses()[0]

    history += [
        first.message,  # replayed unchanged, thinking blocks included
        Message(
            role=Role.USER, content=[ToolResultBlock(tool_use_id=call.id, content="24°C, sunny")]
        ),
    ]
    second = await provider.generate(ModelRequest(model=model, messages=history, tools=[tool]))
    assert second.stop_reason is StopReason.END_TURN
    assert "24" in second.message.plain_text()
