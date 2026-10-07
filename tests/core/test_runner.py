"""Tests for core/runner.py — the reasoning loop, driven entirely by fakes.

No network, no SDK: a scripted ModelProvider, a fixed registry and a recording
executor. That the loop is testable this way is the point of the layering.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from agent.core.context import Prompt
from agent.core.run import RunContext, RunStop
from agent.core.runner import ReasoningLoop
from agent.core.tools import ToolResult
from agent.domain.agent import (
    AgentSpec,
    Effect,
    InboundEvent,
    Message,
    PendingAction,
    Role,
    RunLimits,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
)
from agent.platform.model import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    OpaqueBlock,
    StopReason,
    ToolResultBlock,
    Usage,
)

# --- fakes ------------------------------------------------------------------------


class ScriptedModel:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, *responses: ModelResponse | Exception):
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class StaticContext:
    def build(self, ctx: RunContext) -> Prompt:
        return Prompt(stable=f"You are {ctx.spec.name}.", volatile="Today is Monday.")


class NoArgs(BaseModel):
    pass


class FakeTool:
    def __init__(self, name: str, effect: Effect = Effect.READ):
        self.spec = ToolSpec(
            name=name, description=f"{name} tool", input_schema={"type": "object"}, effect=effect
        )
        self.args_model = NoArgs

    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult:
        raise AssertionError("the loop must go through the executor, never call tools directly")


class StaticRegistry:
    def __init__(self, *tools: FakeTool):
        self._tools = list(tools)

    def for_context(self, ctx: RunContext) -> list[FakeTool]:
        return self._tools


class RecordingExecutor:
    """Answers each call by tool name and records (call, ctx)."""

    def __init__(self, results: dict[str, ToolResult] | None = None):
        self._results = results or {}
        self.calls: list[tuple[ToolUseBlock, RunContext]] = []

    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        self.calls.append((call, ctx))
        return self._results.get(call.name, ToolResult(content=f"{call.name} ok"))


# --- builders ---------------------------------------------------------------------


def answer(text: str, stop: StopReason = StopReason.END_TURN, **usage: int) -> ModelResponse:
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=[TextBlock(text=text)]),
        stop_reason=stop,
        usage=Usage(input_tokens=usage.get("i", 10), output_tokens=usage.get("o", 2)),
        model="m",
    )


def tool_calls(*names: str, extra: list[Any] | None = None) -> ModelResponse:
    blocks: list[Any] = list(extra or [])
    blocks += [ToolUseBlock(id=f"tu_{n}", name=n, input={}) for n in names]
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=blocks),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input_tokens=10, output_tokens=2),
        model="m",
    )


def parked(name: str) -> ToolResult:
    call = ToolUseBlock(id=f"tu_{name}", name=name, input={})
    action = PendingAction(id=f"pa_{name}", session_id="s1", principal_id="u1", call=call)
    return ToolResult(content="Not run yet: awaiting approval. Stop here.", pending=action)


SPEC = AgentSpec(
    name="calendar agent",
    instructions="Help with the calendar.",
    model="claude-test",
    limits=RunLimits(max_steps=3, max_tokens=500),
)
EVENT = InboundEvent(event_id="e1", tenant_id="u1", session_id="s1", principal_id="u1", text="hi")
CTX = RunContext.for_event(SPEC, EVENT)


def loop(
    model: ScriptedModel,
    executor: RecordingExecutor | None = None,
    registry: StaticRegistry | None = None,
) -> ReasoningLoop:
    return ReasoningLoop(
        model=model,
        context=StaticContext(),
        tools=registry or StaticRegistry(FakeTool("calendar__list"), FakeTool("calendar__create")),
        executor=executor or RecordingExecutor(),
    )


# --- plain answers ----------------------------------------------------------------


async def test_plain_answer_completes_in_one_step():
    model = ScriptedModel(answer("Hello!"))
    result = await loop(model).run(CTX, EVENT, history=[])

    assert result.stop is RunStop.COMPLETED
    assert result.output == "Hello!"
    assert result.steps == 1
    user, assistant = result.new_messages
    assert (user.role, user.plain_text(), user.event_id) == (Role.USER, "hi", "e1")
    assert assistant.plain_text() == "Hello!"


async def test_request_is_built_from_spec_context_and_registry():
    model = ScriptedModel(answer("ok"))
    await loop(model, registry=StaticRegistry(FakeTool("cal", Effect.WRITE))).run(CTX, EVENT, [])

    req = model.requests[0]
    assert req.model == "claude-test"
    assert req.max_tokens == 500
    assert [(b.text, b.cache) for b in req.system] == [
        ("You are calendar agent.", True),  # stable: cache breakpoint after it
        ("Today is Monday.", False),  # volatile: after the breakpoint
    ]
    assert req.cache is True
    assert [t.name for t in req.tools] == ["cal"]
    assert "effect" not in req.tools[0].model_dump()  # never reaches the model


async def test_history_goes_first_and_is_not_mutated():
    history = [Message.text(Role.USER, "earlier"), Message.text(Role.ASSISTANT, "reply")]
    snapshot = list(history)
    model = ScriptedModel(answer("ok"))
    result = await loop(model).run(CTX, EVENT, history)

    assert history == snapshot
    assert model.requests[0].messages[:2] == history
    assert model.requests[0].messages[2].plain_text() == "hi"
    assert len(result.new_messages) == 2  # history is not part of new_messages


# --- tool rounds ------------------------------------------------------------------


async def test_tool_round_then_answer():
    model = ScriptedModel(tool_calls("calendar__list"), answer("You have 2 meetings."))
    executor = RecordingExecutor({"calendar__list": ToolResult(content="2 events")})
    result = await loop(model, executor).run(CTX, EVENT, [])

    assert result.stop is RunStop.COMPLETED
    assert result.output == "You have 2 meetings."
    assert result.steps == 2
    assert result.usage == Usage(input_tokens=20, output_tokens=4)

    ((call, ctx),) = executor.calls
    assert call.name == "calendar__list" and ctx is CTX

    roles = [m.role for m in result.new_messages]
    assert roles == [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT]
    assert result.new_messages[2].content == [
        ToolResultBlock(tool_use_id="tu_calendar__list", content="2 events")
    ]
    # the second request carries the whole round so far
    assert model.requests[1].messages == result.new_messages[:3]


async def test_assistant_turn_is_replayed_unchanged():
    thinking = OpaqueBlock(provider="anthropic", raw={"type": "thinking", "signature": "s"})
    first = tool_calls("calendar__list", extra=[thinking])
    model = ScriptedModel(first, answer("done"))
    await loop(model).run(CTX, EVENT, [])

    assert model.requests[1].messages[1] is first.message


async def test_parallel_calls_return_in_one_user_message_in_order():
    model = ScriptedModel(tool_calls("calendar__list", "calendar__create"), answer("done"))
    result = await loop(model).run(CTX, EVENT, [])

    results = result.new_messages[2].content
    assert [r.tool_use_id for r in results] == ["tu_calendar__list", "tu_calendar__create"]


async def test_tool_errors_are_passed_to_the_model():
    model = ScriptedModel(tool_calls("calendar__list"), answer("Sorry, the calendar failed."))
    executor = RecordingExecutor({"calendar__list": ToolResult.error("Calendar is unreachable.")})
    result = await loop(model, executor).run(CTX, EVENT, [])

    block = result.new_messages[2].content[0]
    assert block.is_error is True and block.content == "Calendar is unreachable."
    assert result.stop is RunStop.COMPLETED


# --- approvals --------------------------------------------------------------------


async def test_parked_call_ends_the_run_without_another_model_call():
    model = ScriptedModel(tool_calls("calendar__create"))
    executor = RecordingExecutor({"calendar__create": parked("calendar__create")})
    result = await loop(model, executor).run(CTX, EVENT, [])

    assert result.stop is RunStop.AWAITING_APPROVAL
    assert [p.id for p in result.pending] == ["pa_calendar__create"]
    assert len(model.requests) == 1
    # transcript stays valid: the tool_use has its tool_result
    assert result.new_messages[-1].content[0].tool_use_id == "tu_calendar__create"


async def test_a_round_with_one_parked_call_still_runs_the_others():
    model = ScriptedModel(tool_calls("calendar__list", "calendar__create"))
    executor = RecordingExecutor({"calendar__create": parked("calendar__create")})
    result = await loop(model, executor).run(CTX, EVENT, [])

    assert [c.name for c, _ in executor.calls] == ["calendar__list", "calendar__create"]
    assert [b.content for b in result.new_messages[-1].content][0] == "calendar__list ok"
    assert result.stop is RunStop.AWAITING_APPROVAL


# --- limits and other stops -------------------------------------------------------


async def test_max_steps_stops_the_loop_with_a_valid_transcript():
    model = ScriptedModel(*(tool_calls("calendar__list") for _ in range(3)))
    result = await loop(model).run(CTX, EVENT, [])

    assert result.stop is RunStop.LIMIT_REACHED
    assert result.steps == 3
    assert len(model.requests) == 3
    assert result.new_messages[-1].role is Role.USER  # ends on the tool results
    assert result.output == ""  # no text from the model; the edge picks the fallback


@pytest.mark.parametrize(
    ("reason", "stop"),
    [
        (StopReason.MAX_TOKENS, RunStop.LIMIT_REACHED),
        (StopReason.REFUSAL, RunStop.REFUSED),
        (StopReason.OTHER, RunStop.INCOMPLETE),
    ],
)
async def test_non_tool_stops_map_to_run_stops(reason: StopReason, stop: RunStop):
    model = ScriptedModel(answer("partial", stop=reason))
    result = await loop(model).run(CTX, EVENT, [])
    assert result.stop is stop
    assert result.output == "partial"


async def test_tool_use_cut_by_max_tokens_is_not_executed():
    truncated = tool_calls("calendar__create").model_copy(
        update={"stop_reason": StopReason.MAX_TOKENS}
    )
    executor = RecordingExecutor()
    result = await loop(ScriptedModel(truncated), executor).run(CTX, EVENT, [])

    assert executor.calls == []
    assert result.stop is RunStop.LIMIT_REACHED


async def test_model_errors_propagate_to_the_caller():
    model = ScriptedModel(ModelProviderError("overloaded", retryable=True, status_code=529))
    with pytest.raises(ModelProviderError):
        await loop(model).run(CTX, EVENT, [])
