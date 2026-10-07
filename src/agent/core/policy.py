"""L2 · Policy — who may run which tool call (tb-agent docs 15, 27).

The interface and the building blocks here are kernel; the rules are product. For
a WhatsApp business bot they will be customer-scoping (a record must belong to the
sender) on top of ``ConfirmWrites``; a restaurant would add tenant isolation and
order-state rules. The ToolExecutor asks Policy before every call, after the
arguments have been validated.
"""

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agent.core.run import RunContext
from agent.domain.agent import Effect, ToolSpec, ToolUseBlock


class Allow(BaseModel):
    model_config = ConfigDict(frozen=True)


class Deny(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str  # shown to the model, so it can explain or try something else


class RequireApproval(BaseModel):
    model_config = ConfigDict(frozen=True)


Decision = Allow | Deny | RequireApproval


class Policy(Protocol):
    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision: ...


# --- Building blocks --------------------------------------------------------------


class AllowAll:
    """Every call runs. For tests and local experiments only."""

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        return Allow()


class ConfirmWrites:
    """Reads run; writes need a human's approval unless the tool is auto-approved
    (the "always" answer on an approval card)."""

    def __init__(self, auto_approved: Iterable[str] = ()) -> None:
        self._auto_approved = frozenset(auto_approved)

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        if spec.effect is Effect.READ or spec.name in self._auto_approved:
            return Allow()
        return RequireApproval()


class AllOf:
    """Combines policies: any Deny wins, then any RequireApproval, else Allow.

    Every policy is asked, in order, until one denies. A product composes its rules
    with the kernel's: ``AllOf(SenderScoped(...), ConfirmWrites(...))``.
    """

    def __init__(self, *policies: Policy) -> None:
        self._policies = policies

    def authorize(self, call: ToolUseBlock, spec: ToolSpec, ctx: RunContext) -> Decision:
        needs_approval = False
        for policy in self._policies:
            decision = policy.authorize(call, spec, ctx)
            if isinstance(decision, Deny):
                return decision
            needs_approval = needs_approval or isinstance(decision, RequireApproval)
        return RequireApproval() if needs_approval else Allow()
