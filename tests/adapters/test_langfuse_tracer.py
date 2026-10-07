"""Tests for adapters/tracing/langfuse.py — the port mapped onto a fake SDK client.

The fake mirrors the slice of the SDK the adapter touches (start_observation /
update / end), so these tests pin the mapping: names → observation types,
input/output passed through, everything else into metadata, errors recorded,
nesting via the running task.
"""

import pytest

from agent.adapters.tracing.langfuse import LangfuseTracer


class FakeObservation:
    def __init__(self, name: str, as_type: str):
        self.name, self.as_type = name, as_type
        self.updates: list[dict] = []
        self.children: list[FakeObservation] = []
        self.ended = False

    def start_observation(self, *, name: str, as_type: str) -> "FakeObservation":
        child = FakeObservation(name, as_type)
        self.children.append(child)
        return child

    def update(self, **fields):
        self.updates.append(fields)

    def end(self):
        self.ended = True


class FakeClient(FakeObservation):
    def __init__(self):
        super().__init__("<client>", "<client>")


def merged(observation: FakeObservation) -> dict:
    out: dict = {}
    for update in observation.updates:
        out.update(update)
    return out


def test_names_map_onto_observation_types_and_spans_nest():
    client = FakeClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]
    with tracer.span("agent.run", agent="whatsapp-assistant"):
        with tracer.span("model.generate", model="m-1"):
            pass
        with tracer.span("tool.execute", tool="pedidos__listar"):
            pass
        with tracer.span("anything.else"):
            pass

    (run,) = client.children
    assert (run.name, run.as_type) == ("agent.run", "agent")
    assert [(c.name, c.as_type) for c in run.children] == [
        ("model.generate", "generation"),
        ("tool.execute", "tool"),
        ("anything.else", "span"),
    ]
    assert run.ended and all(c.ended for c in run.children)


def test_input_output_pass_through_and_the_rest_is_metadata():
    client = FakeClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]
    with tracer.span("tool.execute", tool="pedidos__crear", input={"items": ["1 pozole"]}) as span:
        span.set(output="Pedido pedido-1 creado.", is_error=False)

    (tool,) = client.children
    fields = merged(tool)
    assert fields["input"] == {"items": ["1 pozole"]}
    assert fields["output"] == "Pedido pedido-1 creado."
    assert fields["metadata"]["tool"] == "pedidos__crear"
    assert fields["metadata"]["is_error"] is False
    assert "model" not in fields  # only generations carry the model field


def test_generations_carry_the_model_and_usage_goes_to_metadata():
    client = FakeClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]
    with tracer.span("model.generate", model="m-1") as span:
        span.set(input_tokens=10, output_tokens=2, cost_usd=0.001)

    (gen,) = client.children
    fields = merged(gen)
    assert fields["model"] == "m-1"
    assert fields["metadata"]["input_tokens"] == 10 and fields["metadata"]["cost_usd"] == 0.001


def test_an_exception_is_recorded_and_re_raised_and_the_span_still_ends():
    client = FakeClient()
    tracer = LangfuseTracer(client)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        with tracer.span("tool.execute", tool="t"):
            raise ValueError("boom")

    (tool,) = client.children
    fields = merged(tool)
    assert fields["level"] == "ERROR" and "ValueError: boom" in fields["status_message"]
    assert tool.ended
