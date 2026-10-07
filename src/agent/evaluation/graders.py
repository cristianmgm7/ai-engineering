"""Deterministic graders: each one checks one thing about a run and explains a failure.

They read the ``RunResult`` only: the model's tool calls are the ``tool_use``
blocks in ``new_messages`` (including calls that were parked or refused), and
``output`` is the model's last text. LLM-as-judge graders come later, for what a
rule can't check.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.core.run import RunResult, RunStop
from agent.domain.agent import ToolUseBlock


@dataclass(frozen=True)
class Score:
    grader: str
    passed: bool
    detail: str = ""


class Grader(Protocol):
    def grade(self, result: RunResult) -> Score: ...


def tool_calls(result: RunResult) -> list[ToolUseBlock]:
    return [c for m in result.new_messages for c in m.tool_uses()]


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower()
    return actual == expected


@dataclass(frozen=True)
class CalledTool:
    """The model called ``name``; string args match by case-insensitive substring."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def grade(self, result: RunResult) -> Score:
        label = f"called {self.name}" + (f" with {self.args}" if self.args else "")
        calls = [c for c in tool_calls(result) if c.name == self.name]
        if not calls:
            made = [c.name for c in tool_calls(result)] or "none"
            return Score(label, False, f"calls made: {made}")
        for c in calls:
            if all(_matches(c.input.get(k), v) for k, v in self.args.items()):
                return Score(label, True)
        return Score(label, False, f"inputs were: {[c.input for c in calls]}")


@dataclass(frozen=True)
class NotCalledTool:
    """The model did not call ``name`` (or any tool, when ``name`` is None)."""

    name: str | None = None

    def grade(self, result: RunResult) -> Score:
        made = [c.name for c in tool_calls(result)]
        bad = made if self.name is None else [n for n in made if n == self.name]
        label = f"did not call {self.name or 'any tool'}"
        return Score(label, not bad, f"calls made: {made}" if bad else "")


@dataclass(frozen=True)
class Stopped:
    stop: RunStop

    def grade(self, result: RunResult) -> Score:
        label = f"stopped with {self.stop.value}"
        ok = result.stop is self.stop
        return Score(label, ok, "" if ok else f"stopped with {result.stop.value}")


@dataclass(frozen=True)
class OutputContains:
    """Case-insensitive. All phrases must appear, or at least one with ``any_of``."""

    phrases: tuple[str, ...]
    any_of: bool = False

    def grade(self, result: RunResult) -> Score:
        text = result.output.lower()
        hits = [p for p in self.phrases if p.lower() in text]
        ok = bool(hits) if self.any_of else len(hits) == len(self.phrases)
        label = f"output contains {'any of ' if self.any_of else ''}{list(self.phrases)}"
        return Score(label, ok, "" if ok else f"output: {result.output!r}")


@dataclass(frozen=True)
class OutputExcludes:
    phrases: tuple[str, ...]

    def grade(self, result: RunResult) -> Score:
        text = result.output.lower()
        found = [p for p in self.phrases if p.lower() in text]
        label = f"output excludes {list(self.phrases)}"
        return Score(label, not found, f"found {found} in {result.output!r}" if found else "")


@dataclass(frozen=True)
class MaxWords:
    """Voice replies are heard, not read: keep them short."""

    limit: int

    def grade(self, result: RunResult) -> Score:
        words = len(re.findall(r"\S+", result.output))
        return Score(f"at most {self.limit} words", words <= self.limit, f"{words} words")


@dataclass(frozen=True)
class MaxSteps:
    limit: int

    def grade(self, result: RunResult) -> Score:
        return Score(
            f"at most {self.limit} steps", result.steps <= self.limit, f"{result.steps} steps"
        )
