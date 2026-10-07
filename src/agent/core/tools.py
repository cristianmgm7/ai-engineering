"""L2 · Tools — the contracts for tools, the registry and the executor (tb-agent doc 15).

``ToolExecutor`` is **the single permission boundary**: every tool call the model
makes goes through it. ``PolicyExecutor`` checks, in this order:

1. the tool exists **and this agent may see it** (re-checked, never trusted from
   the model: it could name a tool it was never offered);
2. the arguments validate against the tool's Pydantic model;
3. ``Policy`` allows it, denies it, or requires approval (then it is parked);
4. only then the handler runs, and any exception becomes a readable error.

Concrete tools live in ``adapters/`` and implement ``Tool``; ``FunctionTool`` and
``@tool`` are the kernel's way to write one from a typed async function.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.core.policy import Deny, Policy, RequireApproval
from agent.core.run import RunContext
from agent.domain.agent import Effect, PendingAction, ToolSpec, ToolUseBlock

logger = logging.getLogger(__name__)


class ToolResult(BaseModel):
    """What a tool call returns. ``content`` is compact and written for the model to read.

    When the executor parks a call for approval instead of running it, ``pending``
    holds the parked action and ``content`` only tells the model to stop; the
    harness, not the model, asks the human.
    """

    model_config = ConfigDict(frozen=True)

    content: str
    is_error: bool = False
    pending: PendingAction | None = None

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)


class Tool(Protocol):
    spec: ToolSpec
    args_model: type[BaseModel]  # input_schema is generated from it; args are validated against it

    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult: ...


class ToolRegistry(Protocol):
    def for_context(self, ctx: RunContext) -> list[Tool]:
        """The tools this agent and principal may see on this run."""
        ...


class ToolExecutor(Protocol):
    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        """Authorize and run one call. Never raises for tool failures: unknown tools,
        bad arguments, denials and handler exceptions all come back as error results."""
        ...


class ApprovalParker(Protocol):
    """The slice of ``ApprovalGate`` the executor needs.

    Declared here because ``approval.py`` imports ``ToolResult`` from this module;
    any ``ApprovalGate`` satisfies it structurally.
    """

    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction: ...


# --- Writing tools ----------------------------------------------------------------

Handler = Callable[[Any, RunContext], Awaitable[ToolResult | str]]


class FunctionTool:
    """A ``Tool`` made from an async function ``(args, ctx) -> ToolResult | str``."""

    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Handler,
        effect: Effect = Effect.READ,
        input_schema: dict[str, Any] | None = None,  # override the generated schema
    ) -> None:
        self.spec = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema or args_model.model_json_schema(),
            effect=effect,
        )
        self.args_model = args_model
        self._handler = handler

    async def run(self, args: BaseModel, ctx: RunContext) -> ToolResult:
        out = await self._handler(args, ctx)
        return out if isinstance(out, ToolResult) else ToolResult(content=out)


def tool(
    name: str, description: str, effect: Effect = Effect.READ
) -> Callable[[Handler], FunctionTool]:
    """Decorator: the args model is read from the handler's first parameter annotation.

    @tool("calendar__list", "List today's events.")
    async def list_events(args: ListArgs, ctx: RunContext) -> str: ...
    """

    def wrap(handler: Handler) -> FunctionTool:
        first = next(iter(inspect.signature(handler).parameters))
        args_model = get_type_hints(handler).get(first)
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"{handler.__name__}: first parameter must be a Pydantic model")
        return FunctionTool(name, description, args_model, handler, effect)

    return wrap


# --- Registry ---------------------------------------------------------------------


class StaticToolRegistry:
    """A fixed set of tools; each agent sees only those named in its ``AgentSpec``.

    The secure default: an agent with no ``tool_names`` gets no tools. Names in a
    spec that no tool has are ignored here (catch them when the spec is saved).
    """

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            if t.spec.name in self._tools:
                raise ValueError(f"duplicate tool name: {t.spec.name}")
            self._tools[t.spec.name] = t

    def for_context(self, ctx: RunContext) -> list[Tool]:
        return [self._tools[n] for n in ctx.spec.tool_names if n in self._tools]


# --- Executor (the boundary) ------------------------------------------------------

PARKED_CONTENT = (
    "Not run yet: the system is asking the user to approve this exact call. "
    "Stop here; don't ask them yourself."
)


def parked_result(action: PendingAction) -> ToolResult:
    """The result a parked call gets. Shared so evals park exactly like production."""
    return ToolResult(content=PARKED_CONTENT, pending=action)


class PolicyExecutor:
    """The kernel ``ToolExecutor``: lookup → validate → authorize → park or run.

    ``execute_approved`` is the same pipeline for a call a human already approved:
    visibility, arguments and ``Deny`` are checked again (permissions may have
    changed while it waited), and only ``RequireApproval`` is treated as satisfied.
    """

    def __init__(self, registry: ToolRegistry, policy: Policy, approvals: ApprovalParker) -> None:
        self._registry = registry
        self._policy = policy
        self._approvals = approvals

    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        return await self._execute(call, ctx, approved=False)

    async def execute_approved(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        return await self._execute(call, ctx, approved=True)

    async def _execute(self, call: ToolUseBlock, ctx: RunContext, approved: bool) -> ToolResult:
        visible = {t.spec.name: t for t in self._registry.for_context(ctx)}
        found = visible.get(call.name)
        if found is None:
            return ToolResult.error(
                f"Unknown tool: {call.name}. Use only the tools you were given."
            )

        try:
            args = found.args_model.model_validate(call.input)
        except ValidationError as e:
            return ToolResult.error(f"Invalid arguments for {call.name}: {_summarize(e)}")

        decision = self._policy.authorize(call, found.spec, ctx)
        if isinstance(decision, Deny):
            return ToolResult.error(f"Not allowed: {decision.reason}")
        if isinstance(decision, RequireApproval) and not approved:
            return parked_result(await self._approvals.park(call, ctx))

        try:
            return await found.run(args, ctx)
        except Exception as e:
            logger.exception("tool %s failed", call.name)
            return ToolResult.error(
                f"{call.name} failed ({type(e).__name__}). Tell the user it didn't work."
            )


def _summarize(error: ValidationError) -> str:
    parts = []
    for err in error.errors(include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "input"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)
