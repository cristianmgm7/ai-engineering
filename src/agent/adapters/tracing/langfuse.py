"""L3 · LangfuseTracer — the ``Tracer`` port over the Langfuse SDK (v4).

Thin by design: the kernel's decorators decide *what* gets traced and which
attributes exist; this adapter only maps the port onto the SDK. Span names map
onto Langfuse observation types (``model.generate`` → generation, so token and
cost analytics light up). ``input``/``output`` pass through as themselves;
every other attribute lands in ``metadata``, which keeps the adapter immune to
SDK keyword drift. Nesting follows the running task with a ``ContextVar``,
like ``InMemoryTracer``.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from langfuse import Langfuse, propagate_attributes

from agent.platform.config import Settings

_TYPES = {"agent.run": "agent", "model.generate": "generation", "tool.execute": "tool"}
_DIRECT = ("input", "output")
# Kernel usage attributes → Langfuse usage_details keys (Anthropic-style cache names,
# so the pricing table recognizes them). Only generations get native usage.
_USAGE = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_write_tokens": "cache_creation_input_tokens",
}


class _Observation(Protocol):
    """The slice of a Langfuse observation this adapter touches (also fakeable)."""

    def start_observation(self, *, name: str, as_type: str) -> "_Observation": ...

    def update(self, **fields: Any) -> None: ...

    def end(self) -> None: ...


class _LangfuseSpan:
    """Adapts one Langfuse observation to the port's ``Span``.

    Metadata accumulates here and the full dict goes with every update, so the
    mapping doesn't depend on whether the SDK merges or replaces metadata.
    """

    def __init__(self, observation: _Observation, as_type: str) -> None:
        self._observation = observation
        self._type = as_type
        self._metadata: dict[str, Any] = {}

    def set(self, **attributes: Any) -> None:
        if not attributes:
            return
        fields: dict[str, Any] = {k: attributes[k] for k in _DIRECT if k in attributes}
        consumed = set(fields)
        if self._type == "generation":
            if "model" in attributes:
                fields["model"] = attributes["model"]
                consumed.add("model")
            usage = {to: attributes[k] for k, to in _USAGE.items() if k in attributes}
            if usage:
                fields["usage_details"] = usage
                consumed.update(k for k in _USAGE if k in attributes)
            if "cost_usd" in attributes:
                fields["cost_details"] = {"total": attributes["cost_usd"]}
                consumed.add("cost_usd")
        rest = {k: v for k, v in attributes.items() if k not in consumed}
        if rest:
            self._metadata.update(rest)
            fields["metadata"] = dict(self._metadata)
        self._observation.update(**fields)


class LangfuseTracer:
    def __init__(
        self,
        client: Langfuse,
        propagate: Callable[..., AbstractContextManager[Any]] = propagate_attributes,
    ) -> None:
        self._client = client
        self._propagate = propagate  # injectable so tests can record it
        self._current: ContextVar[_Observation | None] = ContextVar(
            f"langfuse-span-{id(self)}", default=None
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "LangfuseTracer":
        assert settings.langfuse_public_key and settings.langfuse_secret_key
        return cls(
            Langfuse(
                public_key=settings.langfuse_public_key.get_secret_value(),
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                base_url=settings.langfuse_base_url.rstrip("/"),
            )
        )

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[_LangfuseSpan]:
        as_type = _TYPES.get(name, "span")
        parent = self._current.get()
        with ExitStack() as stack:
            if parent is None:  # a root span names the trace's user and session
                trace = {
                    to: attributes[k]
                    for k, to in (("principal_id", "user_id"), ("session_id", "session_id"))
                    if attributes.get(k)
                }
                if trace:
                    stack.enter_context(self._propagate(**trace))
            observation = (parent or self._client).start_observation(name=name, as_type=as_type)
            span = _LangfuseSpan(observation, as_type)
            span.set(**attributes)
            token = self._current.set(observation)
            try:
                yield span
            except BaseException as e:
                observation.update(level="ERROR", status_message=f"{type(e).__name__}: {e}")
                raise
            finally:
                self._current.reset(token)
                observation.end()

    def flush(self) -> None:
        """Send what's buffered. Call before a short-lived process exits."""
        self._client.flush()
