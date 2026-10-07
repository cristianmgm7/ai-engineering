"""Tests for core/approval.py — parking, deciding once, and who may decide."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from agent.adapters.stores.memory import InMemoryPendingActions
from agent.core.approval import (
    ApprovalOutcome,
    ApprovalStatus,
    StoreApprovalGate,
    decision_turn_text,
)
from agent.core.policy import AllOf, Allow, ConfirmWrites, Decision, Deny
from agent.core.run import RunContext
from agent.core.tools import PolicyExecutor, StaticToolRegistry, ToolResult, tool
from agent.domain.agent import AgentSpec, Effect, ToolSpec, ToolUseBlock

RUNS: list[dict] = []


class EventArgs(BaseModel):
    title: str


@tool("calendar__create", "Create an event.", effect=Effect.WRITE)
async def create_event(args: EventArgs, ctx: RunContext) -> str:
    RUNS.append({"title": args.title, "by": ctx.principal_id})
    return f"Created {args.title}."


@pytest.fixture(autouse=True)
def _reset():
    RUNS.clear()


class MovableClock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at


SPEC = AgentSpec(name="cal", instructions="", model="m", tool_names=["calendar__create"])
OWNER = RunContext(spec=SPEC, tenant_id="u1", session_id="s1", principal_id="u1")
STRANGER = OWNER.model_copy(update={"principal_id": "u2"})
OTHER_SESSION = OWNER.model_copy(update={"session_id": "s2"})
CALL = ToolUseBlock(id="tu1", name="calendar__create", input={"title": "Lunch"})


class Revocable:
    """A policy whose permission can be revoked while a call waits."""

    def __init__(self) -> None:
        self.revoked = False

    def authorize(self, call, spec: ToolSpec, ctx: RunContext) -> Decision:
        return Deny(reason="access was revoked") if self.revoked else Allow()


def wire(policy=None, ttl=timedelta(hours=1)):
    clock = MovableClock()
    gate = StoreApprovalGate(InMemoryPendingActions(), clock, ttl)
    registry = StaticToolRegistry([create_event])
    executor = PolicyExecutor(registry, policy or ConfirmWrites(), gate)
    gate.bind(executor)
    return gate, executor, clock


async def park(executor: PolicyExecutor) -> str:
    parked = await executor.execute(CALL, OWNER)
    assert parked.pending is not None and RUNS == []
    return parked.pending.id


async def test_park_stores_the_exact_call_with_an_expiry():
    gate, executor, clock = wire()
    action_id = await park(executor)
    stored = await gate._store.get(action_id)
    assert stored.call == CALL
    assert (stored.session_id, stored.principal_id) == ("s1", "u1")
    assert stored.expires_at == clock.at + timedelta(hours=1)


async def test_approval_runs_the_parked_input_unchanged_exactly_once():
    gate, executor, _ = wire()
    action_id = await park(executor)

    first = await gate.decide(action_id, True, OWNER)
    second = await gate.decide(action_id, True, OWNER)

    assert first.status is ApprovalStatus.APPROVED
    assert first.result == ToolResult(content="Created Lunch.")
    assert second.status is ApprovalStatus.NOT_FOUND
    assert RUNS == [{"title": "Lunch", "by": "u1"}]


async def test_concurrent_approvals_run_it_once():
    gate, executor, _ = wire()
    action_id = await park(executor)
    outcomes = await asyncio.gather(*(gate.decide(action_id, True, OWNER) for _ in range(5)))
    assert sorted(o.status for o in outcomes).count(ApprovalStatus.APPROVED) == 1
    assert len(RUNS) == 1


async def test_denial_does_not_run():
    gate, executor, _ = wire()
    outcome = await gate.decide(await park(executor), False, OWNER)
    assert outcome.status is ApprovalStatus.DENIED and outcome.action.call == CALL
    assert RUNS == []


async def test_expired_actions_do_not_run():
    gate, executor, clock = wire()
    action_id = await park(executor)
    clock.at += timedelta(hours=1)
    outcome = await gate.decide(action_id, True, OWNER)
    assert outcome.status is ApprovalStatus.EXPIRED
    assert RUNS == []


@pytest.mark.parametrize("decider", [STRANGER, OTHER_SESSION], ids=["stranger", "other-session"])
async def test_only_the_requester_in_the_same_session_may_decide(decider: RunContext):
    gate, executor, _ = wire()
    action_id = await park(executor)

    refused = await gate.decide(action_id, True, decider)
    assert refused == ApprovalOutcome(status=ApprovalStatus.NOT_ALLOWED)
    assert RUNS == []
    # the stranger did not consume it: the requester can still decide
    assert (await gate.decide(action_id, True, OWNER)).status is ApprovalStatus.APPROVED


async def test_unknown_action():
    gate, _, _ = wire()
    assert (await gate.decide("nope", True, OWNER)).status is ApprovalStatus.NOT_FOUND


async def test_permissions_are_checked_again_at_approval_time():
    revocable = Revocable()
    gate, executor, _ = wire(policy=AllOf(revocable, ConfirmWrites()))
    action_id = await park(executor)

    revocable.revoked = True
    outcome = await gate.decide(action_id, True, OWNER)

    assert outcome.status is ApprovalStatus.APPROVED  # the human said yes...
    assert outcome.result.is_error  # ...but the boundary still refused
    assert "revoked" in outcome.result.content
    assert RUNS == []


async def test_a_tool_removed_from_the_agent_while_waiting_is_refused():
    gate, executor, _ = wire()
    action_id = await park(executor)
    without_tool = OWNER.model_copy(update={"spec": SPEC.model_copy(update={"tool_names": []})})
    outcome = await gate.decide(action_id, True, without_tool)
    assert outcome.result.is_error and "Unknown tool" in outcome.result.content
    assert RUNS == []


async def test_an_unbound_gate_fails_loudly():
    gate = StoreApprovalGate(InMemoryPendingActions(), MovableClock())
    with pytest.raises(RuntimeError):
        await gate.decide("x", True, OWNER)


# --- decision_turn_text -----------------------------------------------------------


async def test_approved_turn_text_carries_the_real_result_as_data():
    gate, executor, _ = wire()
    outcome = await gate.decide(await park(executor), True, OWNER)
    text = decision_turn_text(outcome)
    assert text.startswith("[Automatic system message.")
    assert "approved" in text and "Created Lunch." in text and "succeeded" in text
    assert '"title": "Lunch"' in text
    assert "Do not run this action again." in text


async def test_denied_and_expired_turn_texts():
    gate, executor, clock = wire()
    denied = await gate.decide(await park(executor), False, OWNER)
    assert "declined" in decision_turn_text(denied)

    action_id = await park(executor)
    clock.at += timedelta(days=2)
    assert "expired" in decision_turn_text(await gate.decide(action_id, True, OWNER))


def test_refusals_never_reach_the_model():
    with pytest.raises(ValueError):
        decision_turn_text(ApprovalOutcome(status=ApprovalStatus.NOT_ALLOWED))


async def test_waiting_returns_the_newest_live_action_of_this_principal_and_session():
    gate, executor, clock = wire()
    assert await gate.waiting(OWNER) is None
    first = await park(executor)
    clock.at += timedelta(minutes=1)
    second = await park(executor)

    assert (await gate.waiting(OWNER)).id == second
    assert await gate.waiting(STRANGER) is None
    assert await gate.waiting(OTHER_SESSION) is None

    clock.at += timedelta(hours=2)  # both expired
    assert await gate.waiting(OWNER) is None
    assert first != second
