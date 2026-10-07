"""L2 · The shapes of a run: what goes in (``RunContext``) and what comes out
(``RunResult``).

Kept apart from ``runner.py`` so every core module (tools, policy, approval,
context) can depend on them without importing the loop itself.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.agent import AgentSpec, InboundEvent, Message, PendingAction
from agent.platform.model import Usage


class RunContext(BaseModel):
    """Everything a run needs to know about who and where. Passed explicitly, no globals."""

    model_config = ConfigDict(frozen=True)

    spec: AgentSpec
    tenant_id: str
    session_id: str
    principal_id: str

    @classmethod
    def for_event(cls, spec: AgentSpec, event: InboundEvent) -> "RunContext":
        return cls(
            spec=spec,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            principal_id=event.principal_id,
        )


class RunStop(StrEnum):
    COMPLETED = "completed"  # the model answered (end_turn)
    AWAITING_APPROVAL = "awaiting_approval"  # a tool call was parked for a human
    LIMIT_REACHED = "limit_reached"  # max_steps, or max_tokens cut the answer
    REFUSED = "refused"  # the provider's safety classifiers declined
    INCOMPLETE = "incomplete"  # any other stop (pause, stop sequence)


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stop: RunStop
    output: str  # the model's last text; may be empty. The edge decides what to say
    new_messages: list[Message]  # everything this run appended, user message first
    usage: Usage  # summed over every model call in the run
    steps: int  # model calls made
    pending: list[PendingAction] = Field(default_factory=list)  # set on AWAITING_APPROVAL
