"""L3 · InMemoryTracer — keeps every span in a list. For tests and evals.

Nesting follows the running task through a ``ContextVar``, so concurrent runs
(eval cases in parallel) each get their own parent chain.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count
from typing import Any

_ids = count(1)


@dataclass
class RecordedSpan:
    id: int
    name: str
    parent_id: int | None
    attributes: dict[str, Any]
    started: float
    ended: float | None = None
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        return None if self.ended is None else (self.ended - self.started) * 1000

    def set(self, **attributes: Any) -> None:
        self.attributes.update(attributes)


@dataclass
class InMemoryTracer:
    now: Callable[[], float] = time.perf_counter
    spans: list[RecordedSpan] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._current: ContextVar[int | None] = ContextVar(f"span-{id(self)}", default=None)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[RecordedSpan]:
        rec = RecordedSpan(
            id=next(_ids),
            name=name,
            parent_id=self._current.get(),
            attributes=dict(attributes),
            started=self.now(),
        )
        self.spans.append(rec)
        token = self._current.set(rec.id)
        try:
            yield rec
        except BaseException as e:
            rec.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            rec.ended = self.now()
            self._current.reset(token)

    def named(self, name: str) -> list[RecordedSpan]:
        return [s for s in self.spans if s.name == name]

    def children(self, parent: RecordedSpan) -> list[RecordedSpan]:
        return [s for s in self.spans if s.parent_id == parent.id]
