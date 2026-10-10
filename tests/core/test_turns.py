"""Tests for core/turns.py — whole conversations through the kernel, with a fake model."""

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from agent.adapters.stores.memory import InMemoryPendingActionStore, InMemorySessionStore
from agent.core.approval import ApprovalStatus, StoreApprovalGate
from agent.core.context import InstructionsContext
from agent.core.memory import WindowMemory
from agent.core.policy import ConfirmWrites
from agent.core.run import RunContext, RunStop
from agent.core.runner import ReasoningLoop
from agent.core.tools import PolicyExecutor, StaticToolRegistry, tool
from agent.core.turns import TurnService
from agent.domain.agent import (
    AgentSpec,
    Effect,
    InboundEvent,
    Message,
    Role,
    TextBlock,
    ToolUseBlock,
)
from agent.platform.clock import FixedClock
from agent.platform.model import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    StopReason,
    Usage,
)

CREATED: list[str] = []


class EventArgs(BaseModel):
    title: str


@tool("calendar__create", "Create an event.", effect=Effect.WRITE)
async def create_event(args: EventArgs, ctx: RunContext) -> str:
    CREATED.append(args.title)
    return f"Created {args.title}."


@pytest.fixture(autouse=True)
def _reset():
    CREATED.clear()


def say(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=[TextBlock(text=text)]),
        stop_reason=StopReason.END_TURN,
        usage=Usage(),
        model="m",
    )


def call_create(title: str) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=[ToolUseBlock(id="tu1", name="calendar__create", input={"title": title})],
        ),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(),
        model="m",
    )


class Scripted:
    def __init__(self, *items):
        self.items = list(items)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


SPEC = AgentSpec(name="cal", instructions="Be brief.", model="m", tool_names=["calendar__create"])
OWNER = RunContext(spec=SPEC, tenant_id="u1", session_id="s1", principal_id="u1")


def event(text: str, event_id: str, principal: str = "u1") -> InboundEvent:
    return InboundEvent(
        event_id=event_id, tenant_id=principal, session_id="s1", principal_id=principal, text=text
    )


def service(model: Scripted) -> tuple[TurnService, InMemorySessionStore]:
    clock = FixedClock(datetime(2026, 10, 6, 14, 5, tzinfo=UTC))
    store = InMemorySessionStore()
    gate = StoreApprovalGate(InMemoryPendingActionStore(), clock)
    registry = StaticToolRegistry([create_event])
    executor = PolicyExecutor(registry, ConfirmWrites(), gate)
    gate.bind(executor)
    loop = ReasoningLoop(model, InstructionsContext(clock), registry, executor)
    return TurnService(loop, WindowMemory(store), gate), store


async def test_the_second_turn_sees_the_first_as_text():
    model = Scripted(say("Hi Cristian."), say("You said hello."))
    turns, store = service(model)
    await turns.handle(OWNER, event("hello", "e1"))
    await turns.handle(OWNER, event("what did I say?", "e2"))

    second = model.requests[1].messages
    assert [(m.role.value, m.plain_text()) for m in second] == [
        ("user", "hello"),
        ("assistant", "Hi Cristian."),
        ("user", "what did I say?"),
    ]
    assert len(await store.load("s1")) == 4


async def test_a_duplicate_event_is_skipped():
    model = Scripted(say("Hi."))
    turns, _ = service(model)
    assert await turns.handle(OWNER, event("hello", "e1")) is not None
    assert await turns.handle(OWNER, event("hello", "e1")) is None
    assert len(model.requests) == 1


async def test_a_failed_run_saves_nothing_so_a_retry_runs_again():
    model = Scripted(ModelProviderError("overloaded", retryable=True), say("Hi."))
    turns, store = service(model)
    with pytest.raises(ModelProviderError):
        await turns.handle(OWNER, event("hello", "e1"))
    assert await store.load("s1") == []

    result = await turns.handle(OWNER, event("hello", "e1"))
    assert result is not None and result.output == "Hi."


async def test_approve_then_narrate_the_real_result():
    model = Scripted(call_create("Lunch"), say("Done, lunch is booked."))
    turns, store = service(model)

    parked = await turns.handle(OWNER, event("book lunch", "e1"))
    assert parked.stop is RunStop.AWAITING_APPROVAL and CREATED == []

    decision = await turns.decide(OWNER, parked.pending[0].id, approved=True)

    assert decision.outcome.status is ApprovalStatus.APPROVED
    assert CREATED == ["Lunch"]
    assert decision.run.output == "Done, lunch is booked."
    narration_prompt = model.requests[1].messages[-1].plain_text()
    assert narration_prompt.startswith("[Automatic system message.")
    assert "Created Lunch." in narration_prompt
    assert await store.has_event("s1", f"decision:{parked.pending[0].id}")


async def test_a_stranger_cannot_approve_and_nothing_reaches_the_model():
    model = Scripted(call_create("Lunch"))
    turns, _ = service(model)
    parked = await turns.handle(OWNER, event("book lunch", "e1"))

    stranger = OWNER.model_copy(update={"principal_id": "u2"})
    decision = await turns.decide(stranger, parked.pending[0].id, approved=True)

    assert decision.outcome.status is ApprovalStatus.NOT_ALLOWED
    assert decision.run is None and CREATED == [] and len(model.requests) == 1


async def test_turns_in_one_session_run_one_at_a_time():
    started = asyncio.Event()
    release = asyncio.Event()

    class Slow(Scripted):
        async def generate(self, request: ModelRequest) -> ModelResponse:
            if not started.is_set():
                started.set()
                await release.wait()
            return await super().generate(request)

    model = Slow(say("first"), say("second"))
    turns, _ = service(model)
    first = asyncio.create_task(turns.handle(OWNER, event("one", "e1")))
    await started.wait()
    second = asyncio.create_task(turns.handle(OWNER, event("two", "e2")))
    for _ in range(50):  # plenty of turns of the event loop for the second to get going
        await asyncio.sleep(0)
    assert len(model.requests) == 0  # the first is still waiting; the second hasn't started
    release.set()
    await asyncio.gather(first, second)

    assert [m.plain_text() for m in model.requests[1].messages] == ["one", "first", "two"]
