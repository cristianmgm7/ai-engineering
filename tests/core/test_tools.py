"""Tests for core/tools.py and core/policy.py — the permission boundary.

Most tests here are negative on purpose: the boundary is only as good as the calls
it refuses.
"""

import pytest
from pydantic import BaseModel, Field

from agent.core.context import Prompt
from agent.core.policy import AllOf, Allow, AllowAll, ConfirmWrites, Decision, Deny, RequireApproval
from agent.core.run import RunContext, RunStop
from agent.core.runner import ReasoningLoop
from agent.core.tools import (
    PARKED_CONTENT,
    PolicyExecutor,
    StaticToolRegistry,
    ToolResult,
    tool,
)
from agent.domain.agent import (
    AgentSpec,
    Effect,
    InboundEvent,
    Message,
    PendingAction,
    Role,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
)
from agent.platform.model import ModelRequest, ModelResponse, StopReason, Usage

# --- tools under test -------------------------------------------------------------

RUNS: list[str] = []  # which handlers actually ran


class EventArgs(BaseModel):
    title: str = Field(min_length=1)
    owner_id: str


class NoArgs(BaseModel):
    pass


@tool("calendar__list", "List today's events.")
async def list_events(args: NoArgs, ctx: RunContext) -> str:
    RUNS.append("list")
    return "2 events"


@tool("calendar__create", "Create an event.", effect=Effect.WRITE)
async def create_event(args: EventArgs, ctx: RunContext) -> ToolResult:
    RUNS.append(f"create:{args.title}")
    return ToolResult(content=f"created {args.title}")


@tool("calendar__broken", "Always fails.")
async def broken(args: NoArgs, ctx: RunContext) -> str:
    raise RuntimeError("secret internal detail")


@pytest.fixture(autouse=True)
def _reset_runs():
    RUNS.clear()


class RecordingParker:
    def __init__(self) -> None:
        self.parked: list[ToolUseBlock] = []

    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction:
        self.parked.append(call)
        return PendingAction(
            id=f"pa_{len(self.parked)}",
            session_id=ctx.session_id,
            principal_id=ctx.principal_id,
            call=call,
        )


class OwnerOnly:
    """A product-style rule: a call may only touch resources the sender owns."""

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        owner = call.input.get("owner_id")
        if owner is not None and owner != ctx.principal_id:
            return Deny(reason="that calendar belongs to someone else")
        return Allow()


ALL_TOOLS = [list_events, create_event, broken]
SPEC = AgentSpec(
    name="calendar",
    instructions="",
    model="m",
    tool_names=["calendar__list", "calendar__create", "calendar__broken"],
)
CTX = RunContext(spec=SPEC, tenant_id="u1", session_id="s1", principal_id="u1")


def call(name: str, **input: object) -> ToolUseBlock:
    return ToolUseBlock(id=f"tu_{name}", name=name, input=input)


def executor(policy=None, parker: RecordingParker | None = None) -> PolicyExecutor:
    return PolicyExecutor(
        StaticToolRegistry(ALL_TOOLS), policy or AllowAll(), parker or RecordingParker()
    )


# --- writing tools ----------------------------------------------------------------


def test_tool_decorator_builds_spec_from_the_args_model():
    assert create_event.spec.name == "calendar__create"
    assert create_event.spec.effect is Effect.WRITE
    assert create_event.args_model is EventArgs
    assert create_event.spec.input_schema["required"] == ["title", "owner_id"]


def test_tool_decorator_rejects_a_handler_without_a_pydantic_model():
    with pytest.raises(TypeError):

        @tool("bad", "no model")
        async def bad(args: dict, ctx: RunContext) -> str:
            return ""


async def test_string_results_are_wrapped():
    assert await list_events.run(NoArgs(), CTX) == ToolResult(content="2 events")


# --- registry ---------------------------------------------------------------------


def test_registry_shows_only_the_tools_named_in_the_spec_in_spec_order():
    spec = SPEC.model_copy(update={"tool_names": ["calendar__create", "calendar__list", "nope"]})
    visible = StaticToolRegistry(ALL_TOOLS).for_context(CTX.model_copy(update={"spec": spec}))
    assert [t.spec.name for t in visible] == ["calendar__create", "calendar__list"]


def test_an_agent_without_tool_names_sees_no_tools():
    spec = SPEC.model_copy(update={"tool_names": []})
    assert StaticToolRegistry(ALL_TOOLS).for_context(CTX.model_copy(update={"spec": spec})) == []


def test_registry_rejects_duplicate_names():
    with pytest.raises(ValueError):
        StaticToolRegistry([list_events, list_events])


# --- executor: refusals -----------------------------------------------------------


async def test_unknown_tool_is_refused():
    result = await executor().execute(call("calendar__delete_all"), CTX)
    assert result.is_error and "Unknown tool" in result.content


async def test_a_tool_the_agent_was_not_given_is_refused_even_if_it_exists():
    spec = SPEC.model_copy(update={"tool_names": ["calendar__list"]})
    ctx = CTX.model_copy(update={"spec": spec})
    result = await executor().execute(call("calendar__create", title="x", owner_id="u1"), ctx)
    assert result.is_error and "Unknown tool" in result.content
    assert RUNS == []


async def test_invalid_arguments_are_refused_before_policy_or_handler():
    parker = RecordingParker()
    result = await executor(ConfirmWrites(), parker).execute(
        call("calendar__create", title=""), CTX
    )
    assert result.is_error
    assert "title" in result.content and "owner_id" in result.content
    assert parker.parked == [] and RUNS == []


async def test_denied_call_does_not_run_and_explains_why():
    result = await executor(OwnerOnly()).execute(
        call("calendar__create", title="lunch", owner_id="someone-else"), CTX
    )
    assert result == ToolResult.error("Not allowed: that calendar belongs to someone else")
    assert RUNS == []


async def test_handler_exceptions_become_errors_without_leaking_details():
    result = await executor().execute(call("calendar__broken"), CTX)
    assert result.is_error
    assert "RuntimeError" in result.content
    assert "secret internal detail" not in result.content


# --- executor: approval and success -----------------------------------------------


async def test_require_approval_parks_the_exact_call_and_does_not_run_it():
    parker = RecordingParker()
    c = call("calendar__create", title="lunch", owner_id="u1")
    result = await executor(ConfirmWrites(), parker).execute(c, CTX)

    assert parker.parked == [c]
    assert result.content == PARKED_CONTENT
    assert result.pending is not None and result.pending.call == c
    assert not result.is_error
    assert RUNS == []


async def test_allowed_call_runs_with_validated_args():
    result = await executor().execute(call("calendar__create", title="lunch", owner_id="u1"), CTX)
    assert result == ToolResult(content="created lunch")
    assert RUNS == ["create:lunch"]


# --- policies ---------------------------------------------------------------------

READ = ToolSpec(name="r", description="", input_schema={})
WRITE = ToolSpec(name="w", description="", input_schema={}, effect=Effect.WRITE)


def test_confirm_writes():
    policy = ConfirmWrites(auto_approved=["auto"])
    auto = WRITE.model_copy(update={"name": "auto"})
    assert policy.authorize(call("r"), READ, CTX) == Allow()
    assert policy.authorize(call("w"), WRITE, CTX) == RequireApproval()
    assert policy.authorize(call("auto"), auto, CTX) == Allow()


class Fixed:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        return self.decision


@pytest.mark.parametrize(
    ("decisions", "expected"),
    [
        ([Allow(), Allow()], Allow()),
        ([Allow(), RequireApproval()], RequireApproval()),
        ([RequireApproval(), Deny(reason="no")], Deny(reason="no")),
        ([Deny(reason="first"), Deny(reason="second")], Deny(reason="first")),
        ([], Allow()),
    ],
)
def test_all_of_precedence(decisions: list[Decision], expected: Decision):
    policy = AllOf(*(Fixed(d) for d in decisions))
    assert policy.authorize(call("w"), WRITE, CTX) == expected


async def test_owner_rule_composes_with_confirm_writes():
    policy = AllOf(OwnerOnly(), ConfirmWrites())
    parker = RecordingParker()
    ex = executor(policy, parker)
    denied = await ex.execute(call("calendar__create", title="x", owner_id="other"), CTX)
    parked = await ex.execute(call("calendar__create", title="x", owner_id="u1"), CTX)
    assert denied.is_error and parker.parked == [parked.pending.call]


# --- through the loop -------------------------------------------------------------


class ScriptedModel:
    def __init__(self, *responses: ModelResponse):
        self._responses = list(responses)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return self._responses.pop(0)


class EmptyContext:
    def build(self, ctx: RunContext) -> Prompt:
        return Prompt(stable="")


async def test_a_write_through_the_real_boundary_stops_the_loop_awaiting_approval():
    model = ScriptedModel(
        ModelResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=[
                    TextBlock(text="Booking it."),
                    ToolUseBlock(
                        id="tu1", name="calendar__create", input={"title": "x", "owner_id": "u1"}
                    ),
                ],
            ),
            stop_reason=StopReason.TOOL_USE,
            usage=Usage(),
            model="m",
        )
    )
    registry = StaticToolRegistry(ALL_TOOLS)
    loop = ReasoningLoop(
        model,
        EmptyContext(),
        registry,
        PolicyExecutor(registry, ConfirmWrites(), RecordingParker()),
    )
    event = InboundEvent(
        event_id="e", tenant_id="u1", session_id="s1", principal_id="u1", text="book"
    )
    result = await loop.run(CTX, event, [])

    assert result.stop is RunStop.AWAITING_APPROVAL
    assert result.pending[0].call.name == "calendar__create"
    assert RUNS == []
