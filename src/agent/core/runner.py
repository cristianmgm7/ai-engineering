"""L2 · AgentRunner — the reasoning loop (tb-agent doc 14).

One run = one inbound event. The loop asks the model, runs every tool call it
makes through the ``ToolExecutor`` (the permission boundary), feeds the results
back, and repeats until the model answers, a limit is hit, or a call is parked for
approval. It knows nothing about channels, vendors or storage: the caller passes
the history in and persists ``RunResult.new_messages`` afterwards.

What the loop deliberately does **not** do:

- Phrase anything for the user. ``output`` is the model's own text; fallbacks for
  limits or refusals are the edge's job, because wording is product.
- Ask for approval. A parked call comes back from the executor already parked;
  the loop just stops.
- Catch ``ModelProviderError``. Whether to retry a failed run is the worker's call.
"""

from typing import Protocol

from agent.core.context import ContextBuilder
from agent.core.run import RunContext, RunResult, RunStop
from agent.core.tools import ToolExecutor, ToolRegistry
from agent.domain.agent import InboundEvent, Message, PendingAction, Role
from agent.platform.model import (
    ModelProvider,
    ModelRequest,
    StopReason,
    ToolDefinition,
    ToolResultBlock,
    Usage,
)


class AgentRunner(Protocol):
    async def run(
        self, ctx: RunContext, event: InboundEvent, history: list[Message]
    ) -> RunResult: ...


_STOPS = {
    StopReason.END_TURN: RunStop.COMPLETED,
    StopReason.MAX_TOKENS: RunStop.LIMIT_REACHED,
    StopReason.REFUSAL: RunStop.REFUSED,
}


class ReasoningLoop:
    """The hand-rolled ``AgentRunner``: model call → tool calls → repeat."""

    def __init__(
        self,
        model: ModelProvider,
        context: ContextBuilder,
        tools: ToolRegistry,
        executor: ToolExecutor,
    ) -> None:
        self._model = model
        self._context = context
        self._tools = tools
        self._executor = executor

    async def run(self, ctx: RunContext, event: InboundEvent, history: list[Message]) -> RunResult:
        limits = ctx.spec.limits
        system = self._context.build(ctx).blocks()
        definitions = [_definition(t.spec) for t in self._tools.for_context(ctx)]

        new: list[Message] = [Message.text(Role.USER, event.text, event_id=event.event_id)]
        usage = Usage()

        for step in range(1, limits.max_steps + 1):
            response = await self._model.generate(
                ModelRequest(
                    model=ctx.spec.model,
                    system=system,
                    messages=[*history, *new],
                    tools=definitions,
                    max_tokens=limits.max_tokens,
                    cache=True,  # later steps reuse tools + system + earlier steps
                )
            )
            usage += response.usage
            new.append(response.message)

            if response.stop_reason is not StopReason.TOOL_USE:
                stop = _STOPS.get(response.stop_reason, RunStop.INCOMPLETE)
                return _result(stop, new, usage, step)

            # Every call in the round runs, and all results go back in ONE user
            # message: splitting them teaches the model to stop calling in parallel.
            results: list[ToolResultBlock] = []
            pending: list[PendingAction] = []
            for call in response.message.tool_uses():
                out = await self._executor.execute(call, ctx)
                results.append(
                    ToolResultBlock(tool_use_id=call.id, content=out.content, is_error=out.is_error)
                )
                if out.pending is not None:
                    pending.append(out.pending)
            new.append(Message(role=Role.USER, content=list(results)))

            # A parked call ends the run. The tool_result is already in history, so
            # the transcript stays valid (every tool_use has its tool_result).
            if pending:
                return _result(RunStop.AWAITING_APPROVAL, new, usage, step, pending)

        return _result(RunStop.LIMIT_REACHED, new, usage, limits.max_steps)


def _definition(spec: ToolDefinition) -> ToolDefinition:
    """What the model sees: name, description, schema. ``ToolSpec.effect`` stays home."""
    return ToolDefinition(
        name=spec.name, description=spec.description, input_schema=spec.input_schema
    )


def _result(
    stop: RunStop,
    new: list[Message],
    usage: Usage,
    steps: int,
    pending: list[PendingAction] | None = None,
) -> RunResult:
    last_text = next((m.plain_text() for m in reversed(new) if m.role is Role.ASSISTANT), "")
    return RunResult(
        stop=stop,
        output=last_text,
        new_messages=new,
        usage=usage,
        steps=steps,
        pending=pending or [],
    )
