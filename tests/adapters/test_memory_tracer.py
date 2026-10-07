"""Tests for adapters/tracing/memory.py."""

import asyncio

import pytest

from agent.adapters.tracing.memory import InMemoryTracer
from agent.platform.tracing import NoopTracer


def test_spans_nest_and_record_attributes_and_duration():
    ticks = iter([0.0, 1.0, 3.0, 4.0])
    tracer = InMemoryTracer(now=lambda: next(ticks))
    with tracer.span("agent.run", agent="a") as run:
        with tracer.span("model.generate") as call:
            call.set(output_tokens=5)
        run.set(stop="completed")

    run_span, call_span = tracer.spans
    assert call_span.parent_id == run_span.id and run_span.parent_id is None
    assert run_span.attributes == {"agent": "a", "stop": "completed"}
    assert call_span.attributes == {"output_tokens": 5}
    assert (run_span.duration_ms, call_span.duration_ms) == (4000.0, 2000.0)
    assert tracer.children(run_span) == [call_span]


def test_exceptions_are_recorded_and_re_raised():
    tracer = InMemoryTracer()
    with pytest.raises(ValueError), tracer.span("tool.execute"):
        raise ValueError("boom")
    assert tracer.spans[0].error == "ValueError: boom"
    assert tracer.spans[0].ended is not None


async def test_concurrent_tasks_keep_their_own_parent():
    tracer = InMemoryTracer()

    async def run(name: str) -> None:
        with tracer.span(name):
            await asyncio.sleep(0)
            with tracer.span(f"{name}.child"):
                await asyncio.sleep(0)

    await asyncio.gather(run("a"), run("b"))
    for name in ("a", "b"):
        (parent,) = tracer.named(name)
        assert [c.name for c in tracer.children(parent)] == [f"{name}.child"]


def test_noop_tracer_accepts_everything():
    with NoopTracer().span("x", a=1) as span:
        span.set(b=2)
