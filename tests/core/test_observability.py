"""Tests for core/observability.py — the tracing decorators."""

import pytest

from agent.adapters.tracing.memory import InMemoryTracer
from agent.core.context import Prompt
from agent.core.observability import TracedAgentRunner, TracedModelProvider, TracedToolExecutor
from agent.core.run import RunContext, RunStop
from agent.core.runner import ReasoningLoop
from agent.core.tools import ToolResult
from agent.domain.agent import AgentSpec, InboundEvent, Message, Role, TextBlock, ToolUseBlock
from agent.platform.model import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    StopReason,
    Usage,
)

SPEC = AgentSpec(name="cal", instructions="", model="claude-sonnet-5", tool_names=["t"])
EVENT = InboundEvent(event_id="e", tenant_id="u", session_id="s", principal_id="u", text="hi")
CTX = RunContext.for_event(SPEC, EVENT)


def response(*blocks, stop=StopReason.END_TURN) -> ModelResponse:
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=list(blocks)),
        stop_reason=stop,
        usage=Usage(input_tokens=1_000_000, output_tokens=0),
        model="claude-sonnet-5",
    )


class Scripted:
    def __init__(self, *items):
        self.items = list(items)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Echo:
    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        return ToolResult(content=f"secret result for {call.input['q']}")


class NoTools:
    def for_context(self, ctx):
        return []


class Ctx:
    def build(self, ctx):
        return Prompt(stable="")


def traced_loop(model, tracer, capture=False):
    return TracedAgentRunner(
        ReasoningLoop(
            TracedModelProvider(model, tracer),
            Ctx(),
            NoTools(),
            TracedToolExecutor(Echo(), tracer, capture_content=capture),
        ),
        tracer,
    )


async def test_one_run_produces_a_nested_trace_with_usage_and_cost():
    tracer = InMemoryTracer()
    model = Scripted(
        response(ToolUseBlock(id="1", name="t", input={"q": "x"}), stop=StopReason.TOOL_USE),
        response(TextBlock(text="done")),
    )
    result = await traced_loop(model, tracer).run(CTX, EVENT, [])

    assert result.stop is RunStop.COMPLETED
    (run,) = tracer.named("agent.run")
    assert [s.name for s in tracer.children(run)] == [
        "model.generate",
        "tool.execute",
        "model.generate",
    ]
    assert run.attributes["stop"] == "completed" and run.attributes["steps"] == 2
    assert run.attributes["input_tokens"] == 2_000_000
    assert run.attributes["cost_usd"] == pytest.approx(4.0)
    assert tracer.named("model.generate")[0].attributes["stop_reason"] == "tool_use"


async def test_tool_content_is_only_captured_when_asked():
    for capture in (False, True):
        tracer = InMemoryTracer()
        model = Scripted(
            response(ToolUseBlock(id="1", name="t", input={"q": "x"}), stop=StopReason.TOOL_USE),
            response(TextBlock(text="done")),
        )
        await traced_loop(model, tracer, capture).run(CTX, EVENT, [])
        (tool,) = tracer.named("tool.execute")
        assert tool.attributes["tool"] == "t" and tool.attributes["parked"] is False
        assert ("input" in tool.attributes) is capture
        assert ("output" in tool.attributes) is capture


async def test_approved_calls_leave_a_span_marked_approved():
    class Approvable(Echo):
        async def execute_approved(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
            return ToolResult(content="approved ran")

    tracer = InMemoryTracer()
    executor = TracedToolExecutor(Approvable(), tracer)
    result = await executor.execute_approved(ToolUseBlock(id="1", name="t", input={"q": "x"}), CTX)

    assert result.content == "approved ran"
    (span,) = tracer.named("tool.execute")
    assert span.attributes["approved"] is True and span.attributes["parked"] is False
    # the normal path stays unmarked
    await executor.execute(ToolUseBlock(id="2", name="t", input={"q": "x"}), CTX)
    assert "approved" not in tracer.named("tool.execute")[1].attributes


async def test_model_errors_are_recorded_on_the_span_and_still_raised():
    tracer = InMemoryTracer()
    with pytest.raises(ModelProviderError):
        await traced_loop(Scripted(ModelProviderError("down", retryable=True)), tracer).run(
            CTX, EVENT, []
        )
    assert tracer.named("model.generate")[0].error == "ModelProviderError: down"
    assert tracer.named("agent.run")[0].error is not None
