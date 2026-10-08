"""L2 · Observability decorators — wrap a port with a span, never edit the loop.

Each class implements the same Protocol as the thing it wraps, so wiring is one
line: ``TracedModelProvider(AnthropicModelProvider(...), tracer)``.

Content (tool inputs and outputs) is only recorded with ``capture_content=True``:
production keeps metadata, evals and allow-listed users get full content.
"""

from typing import Protocol

from agent.core.run import RunContext, RunResult
from agent.core.runner import AgentRunner
from agent.core.tools import ToolExecutor, ToolResult
from agent.domain.agent import InboundEvent, Message, ToolUseBlock
from agent.platform.cost import DEFAULT_PRICES, ModelPrice, cost_of
from agent.platform.model import ModelProvider, ModelRequest, ModelResponse, Usage
from agent.platform.tracing import Span, Tracer


def _usage(span: Span, usage: Usage, model: str, prices: dict[str, ModelPrice]) -> None:
    span.set(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cost_usd=cost_of(model, usage, prices),
    )


class TracedModelProvider:
    def __init__(
        self,
        inner: ModelProvider,
        tracer: Tracer,
        prices: dict[str, ModelPrice] = DEFAULT_PRICES,
    ) -> None:
        self._inner, self._tracer, self._prices = inner, tracer, prices

    async def generate(self, request: ModelRequest) -> ModelResponse:
        with self._tracer.span(
            "model.generate",
            model=request.model,
            messages=len(request.messages),
            tools=len(request.tools),
        ) as span:
            response = await self._inner.generate(request)
            span.set(stop_reason=response.stop_reason.value)
            _usage(span, response.usage, response.model, self._prices)
            return response


class ApprovableExecutor(ToolExecutor, Protocol):
    """A ToolExecutor that can also run a call a human already approved."""

    async def execute_approved(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult: ...


class TracedToolExecutor:
    """Wraps both paths through the boundary, so an approved WRITE leaves a span
    too (``approved=True``) — bind the ApprovalGate to this, not to the inner."""

    def __init__(
        self, inner: ApprovableExecutor, tracer: Tracer, capture_content: bool = False
    ) -> None:
        self._inner, self._tracer, self._capture = inner, tracer, capture_content

    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        return await self._traced(self._inner.execute, call, approved=False, ctx=ctx)

    async def execute_approved(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        return await self._traced(self._inner.execute_approved, call, approved=True, ctx=ctx)

    async def _traced(self, run, call: ToolUseBlock, approved: bool, ctx: RunContext) -> ToolResult:
        attributes = {
            "tool": call.name,
            # who and where, so an approved call that runs outside an agent.run
            # still forms an attributed trace of its own
            "principal_id": ctx.principal_id,
            "session_id": ctx.session_id,
            **({"approved": True} if approved else {}),
            **({"input": call.input} if self._capture else {}),
        }
        with self._tracer.span("tool.execute", **attributes) as span:
            result = await run(call, ctx)
            span.set(is_error=result.is_error, parked=result.pending is not None)
            if self._capture:
                span.set(output=result.content)
            return result


class TracedAgentRunner:
    def __init__(
        self,
        inner: AgentRunner,
        tracer: Tracer,
        prices: dict[str, ModelPrice] = DEFAULT_PRICES,
    ) -> None:
        self._inner, self._tracer, self._prices = inner, tracer, prices

    async def run(self, ctx: RunContext, event: InboundEvent, history: list[Message]) -> RunResult:
        with self._tracer.span(
            "agent.run",
            agent=ctx.spec.name,
            model=ctx.spec.model,
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            principal_id=ctx.principal_id,
        ) as span:
            result = await self._inner.run(ctx, event, history)
            span.set(stop=result.stop.value, steps=result.steps)
            _usage(span, result.usage, ctx.spec.model, self._prices)
            return result
