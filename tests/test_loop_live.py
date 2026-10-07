"""Live end-to-end check of the loop: real model, real boundary, fake tool data.
Opt-in: ``uv run pytest -m live``.

Crosses layers on purpose (core + the Anthropic adapter), so it lives at the top
of ``tests/``. Costs a few cents.
"""

import pytest
from pydantic import BaseModel

from agent.adapters.models.anthropic import AnthropicModelProvider
from agent.adapters.tracing.memory import InMemoryTracer
from agent.core.context import Prompt
from agent.core.observability import TracedModelProvider
from agent.core.policy import ConfirmWrites
from agent.core.run import RunContext, RunStop
from agent.core.runner import ReasoningLoop
from agent.core.tools import PolicyExecutor, StaticToolRegistry, tool
from agent.domain.agent import AgentSpec, InboundEvent, PendingAction, ToolUseBlock
from agent.platform.config import get_settings

pytestmark = pytest.mark.live


class CityArgs(BaseModel):
    city: str


@tool("get_weather", "Current weather for a city.")
async def get_weather(args: CityArgs, ctx: RunContext) -> str:
    return "24°C, sunny"


class NoParking:
    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction:
        raise AssertionError("a read tool must never be parked")


class BriefContext:
    def build(self, ctx: RunContext) -> Prompt:
        return Prompt(stable="You are a weather assistant. Answer in one short sentence.")


async def test_loop_calls_a_tool_and_answers_with_its_result():
    get_settings.cache_clear()
    settings = get_settings()
    spec = AgentSpec(
        name="weather", instructions="", model=settings.anthropic_model, tool_names=["get_weather"]
    )
    event = InboundEvent(
        event_id="e1",
        tenant_id="u1",
        session_id="s1",
        principal_id="u1",
        text="What's the weather in Medellín right now?",
    )
    registry = StaticToolRegistry([get_weather])
    loop = ReasoningLoop(
        model=AnthropicModelProvider.from_settings(settings),
        context=BriefContext(),
        tools=registry,
        executor=PolicyExecutor(registry, ConfirmWrites(), NoParking()),
    )
    result = await loop.run(RunContext.for_event(spec, event), event, history=[])

    assert result.stop is RunStop.COMPLETED
    assert result.steps == 2
    assert "24" in result.output


class LongContext:
    """A stable part well above the minimum cacheable prefix (512-4096 tokens)."""

    def build(self, ctx: RunContext) -> Prompt:
        rules = "\n".join(
            f"Rule {i}: when asked about the weather, use get_weather, then answer in one "
            f"short sentence without lists, markdown or emojis."
            for i in range(1, 120)
        )
        return Prompt(stable=f"You are a weather assistant.\n{rules}", volatile="Today is Tuesday.")


async def test_the_second_step_of_a_run_reads_the_cache():
    get_settings.cache_clear()
    settings = get_settings()
    spec = AgentSpec(
        name="weather", instructions="", model=settings.anthropic_model, tool_names=["get_weather"]
    )
    event = InboundEvent(
        event_id="e1",
        tenant_id="u1",
        session_id="s1",
        principal_id="u1",
        text="What's the weather in Cali?",
    )
    tracer = InMemoryTracer()
    registry = StaticToolRegistry([get_weather])
    loop = ReasoningLoop(
        model=TracedModelProvider(AnthropicModelProvider.from_settings(settings), tracer),
        context=LongContext(),
        tools=registry,
        executor=PolicyExecutor(registry, ConfirmWrites(), NoParking()),
    )
    await loop.run(RunContext.for_event(spec, event), event, history=[])

    first, second = tracer.named("model.generate")[:2]
    assert first.attributes["cache_write_tokens"] + first.attributes["cache_read_tokens"] > 0
    assert second.attributes["cache_read_tokens"] > 0
