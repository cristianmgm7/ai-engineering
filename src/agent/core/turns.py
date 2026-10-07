"""L2 · TurnService — one inbound event, start to finish, independent of the channel.

``handle``: skip a duplicate event → load the history window → run → save what the
run appended. ``decide``: let the ApprovalGate decide a parked call, then run one
more turn where the model narrates the real outcome.

Nothing is saved when a run raises (e.g. a ``ModelProviderError``), so the event
was never "seen" and a retry runs it again from scratch.

Runs in the same session are serialized with an in-process lock, so two quick
messages can't both read the same history. With more than one process, the
queue/worker in front of this must serialize per session instead.
"""

import asyncio
from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from agent.core.approval import ApprovalGate, ApprovalOutcome, ApprovalStatus, decision_turn_text
from agent.core.memory import Memory
from agent.core.run import RunContext, RunResult
from agent.core.runner import AgentRunner
from agent.domain.agent import InboundEvent

_NARRATED = {ApprovalStatus.APPROVED, ApprovalStatus.DENIED, ApprovalStatus.EXPIRED}


class DecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: ApprovalOutcome
    run: RunResult | None = None  # the narration turn; None when nothing reached the model


class TurnService:
    def __init__(self, runner: AgentRunner, memory: Memory, approvals: ApprovalGate) -> None:
        self._runner = runner
        self._memory = memory
        self._approvals = approvals
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle(self, ctx: RunContext, event: InboundEvent) -> RunResult | None:
        """Run one turn. Returns None when the event was already handled."""
        async with self._locks[ctx.session_id]:
            if await self._memory.seen(ctx, event.event_id):
                return None
            history = await self._memory.history(ctx)
            result = await self._runner.run(ctx, event, history)
            await self._memory.save(ctx, result.new_messages)
            return result

    async def decide(
        self, ctx: RunContext, action_id: str, approved: bool, event_id: str | None = None
    ) -> DecisionResult:
        """``ctx`` is the decider's: the gate refuses anyone but the requester.

        ``event_id`` is the inbound event that carried the decision (a spoken "yes"),
        so a redelivery of that event is recognised as a duplicate later."""
        outcome = await self._approvals.decide(action_id, approved, ctx)
        if outcome.status not in _NARRATED:
            return DecisionResult(outcome=outcome)
        event = InboundEvent(
            event_id=event_id or f"decision:{action_id}",
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            principal_id=ctx.principal_id,
            text=decision_turn_text(outcome),
        )
        return DecisionResult(outcome=outcome, run=await self.handle(ctx, event))
