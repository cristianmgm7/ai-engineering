"""L4 · Worker — processes one routed event: agent → turn or decision → reply.

1. Resolve the agent (``AgentDirectory``) and build the ``RunContext``.
2. If this person has an approval waiting in this session and what they said is
   a clear yes or no (``ReplyClassifier``), it's a decision: ``TurnService.decide``.
   Anything else is a normal turn: ``TurnService.handle``.
3. Ask the channel's ``Responder`` what to say, and send it through the adapter.

What to say is product: the ``Responder`` owns fallbacks (limits, refusals,
failures) and how an approval is asked. The loop never phrases user text.

``InProcessQueue`` runs events as background tasks in this process. A real queue
(Redis, SQS) goes behind the same ``EventQueue`` port, with one consumer per
session so turns stay in order.
"""

import asyncio
import logging
from typing import Protocol

from agent.core.approval import ApprovalGate, ApprovalOutcome
from agent.core.run import RunContext, RunResult
from agent.core.turns import TurnService
from agent.domain.agent import AgentSpec
from agent.edges.channels import ChannelAdapter, OutboundMessage, RoutedEvent

logger = logging.getLogger(__name__)


class AgentDirectory(Protocol):
    def get(self, agent_key: str) -> AgentSpec | None: ...

    def __contains__(self, agent_key: object) -> bool: ...


class StaticAgentDirectory:
    def __init__(self, agents: dict[str, AgentSpec]) -> None:
        self._agents = dict(agents)

    def get(self, agent_key: str) -> AgentSpec | None:
        return self._agents.get(agent_key)

    def __contains__(self, agent_key: object) -> bool:
        return agent_key in self._agents


class ReplyClassifier(Protocol):
    def classify(self, text: str) -> bool | None:
        """True = yes, False = no, None = not an answer to the approval."""
        ...


class Responder(Protocol):
    def reply(self, result: RunResult) -> str | None:
        """What to say after a run (including how to ask for a parked action)."""
        ...

    def decision(self, outcome: ApprovalOutcome, run: RunResult | None) -> str | None: ...

    def failure(self) -> str: ...


class Worker:
    def __init__(
        self,
        agents: AgentDirectory,
        turns: TurnService,
        approvals: ApprovalGate,
        replies: ReplyClassifier,
        responder: Responder,
        channel: ChannelAdapter,
    ) -> None:
        self._agents = agents
        self._turns = turns
        self._approvals = approvals
        self._replies = replies
        self._responder = responder
        self._channel = channel

    async def process(self, routed: RoutedEvent) -> None:
        spec = self._agents.get(routed.agent_key)
        if spec is None:
            logger.warning(
                "no agent %s; dropping event %s", routed.agent_key, routed.event.event_id
            )
            return
        event = routed.event
        ctx = RunContext.for_event(spec, event)

        try:
            text = await self._respond(ctx, routed)
        except Exception:
            logger.exception("turn failed for event %s", event.event_id)
            text = self._responder.failure()

        if text:
            await self._channel.send(
                OutboundMessage(
                    agent_key=routed.agent_key,
                    session_id=event.session_id,
                    text=text,
                    reply_to_event_id=event.event_id,
                )
            )

    async def _respond(self, ctx: RunContext, routed: RoutedEvent) -> str | None:
        waiting = await self._approvals.waiting(ctx)
        answer = self._replies.classify(routed.event.text) if waiting else None
        if waiting is not None and answer is not None:
            decided = await self._turns.decide(ctx, waiting.id, answer, routed.event.event_id)
            return self._responder.decision(decided.outcome, decided.run)

        result = await self._turns.handle(ctx, routed.event)
        if result is None:  # duplicate delivery: already answered
            return None
        return self._responder.reply(result)


class InProcessQueue:
    """Runs each event as a background task. ``drain`` waits for all of them."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker
        self._tasks: set[asyncio.Task[None]] = set()

    async def enqueue(self, routed: RoutedEvent) -> None:
        task = asyncio.create_task(self._worker.process(routed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks)
