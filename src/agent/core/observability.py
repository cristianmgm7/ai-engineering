"""L2 · Observability decorators — wrap a port with a span, never edit the loop.

Each class implements the same Protocol as the thing it wraps, so wiring is one
line: ``TracedModelProvider(AnthropicModelProvider(...), tracer)``.

Content (tool inputs and outputs) is only recorded with ``capture_content=True``:
production keeps metadata, evals and allow-listed users get full content.
"""

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


class TracedToolExecutor:
    def __init__(self, inner: ToolExecutor, tracer: Tracer, capture_content: bool = False) -> None:
        self._inner, self._tracer, self._capture = inner, tracer, capture_content

    async def execute(self, call: ToolUseBlock, ctx: RunContext) -> ToolResult:
        attributes = {"tool": call.name, **({"input": call.input} if self._capture else {})}
        with self._tracer.span("tool.execute", **attributes) as span:
            result = await self._inner.execute(call, ctx)
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
