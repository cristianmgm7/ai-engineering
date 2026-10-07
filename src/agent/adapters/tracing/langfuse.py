"""L3 · LangfuseTracer — the ``Tracer`` port over the Langfuse SDK (v4).

Thin by design: the kernel's decorators decide *what* gets traced and which
attributes exist; this adapter only maps the port onto the SDK. Span names map
onto Langfuse observation types (``model.generate`` → generation, so token and
cost analytics light up). ``input``/``output`` pass through as themselves;
every other attribute lands in ``metadata``, which keeps the adapter immune to
SDK keyword drift. Nesting follows the running task with a ``ContextVar``,
like ``InMemoryTracer``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from langfuse import Langfuse

from agent.platform.config import Settings

_TYPES = {"agent.run": "agent", "model.generate": "generation", "tool.execute": "tool"}
_DIRECT = ("input", "output")


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
        if self._type == "generation" and "model" in attributes:
            fields["model"] = attributes["model"]
        rest = {k: v for k, v in attributes.items() if k not in fields}
        if rest:
            self._metadata.update(rest)
            fields["metadata"] = dict(self._metadata)
        self._observation.update(**fields)


class LangfuseTracer:
    def __init__(self, client: Langfuse) -> None:
        self._client = client
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
