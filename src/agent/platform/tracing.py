"""L0 · Tracing port — spans for run → model call → tool call.

The kernel only knows this interface. Implementations live in
``adapters/tracing/`` (in-memory for tests and evals; Langfuse later), and the
decorators in ``core/observability.py`` wrap the ports with spans, so the loop
itself never mentions tracing.

A span records an exception automatically when its ``with`` block raises.
"""

from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol


class Span(Protocol):
    def set(self, **attributes: Any) -> None: ...


class Tracer(Protocol):
    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]: ...


class _NoopSpan:
    def set(self, **attributes: Any) -> None:
        pass


class NoopTracer:
    """Records nothing. The default when tracing is off."""

    def span(self, name: str, **attributes: Any) -> AbstractContextManager[Span]:
        return nullcontext(_NoopSpan())
