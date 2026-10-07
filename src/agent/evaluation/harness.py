"""Harness — runs eval cases × reps through the real loop and boundary, then reports.

Each attempt gets its own in-memory tracer, registry and parker, so attempts can
run concurrently without sharing state. Everything but the model and the tool
outputs is production code: ``ReasoningLoop``, ``PolicyExecutor`` with
``ConfirmWrites``, ``InstructionsContext`` and the tracing decorators.
"""

import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from typing import Any

from agent.adapters.tracing.memory import InMemoryTracer, RecordedSpan
from agent.core.context import InstructionsContext
from agent.core.observability import TracedAgentRunner, TracedModelProvider, TracedToolExecutor
from agent.core.policy import ConfirmWrites
from agent.core.run import RunContext, RunResult
from agent.core.runner import ReasoningLoop
from agent.core.tools import PolicyExecutor, StaticToolRegistry
from agent.domain.agent import AgentSpec, InboundEvent, PendingAction, ToolUseBlock
from agent.evaluation.case import EvalCase
from agent.evaluation.graders import Score, tool_calls
from agent.platform.clock import Clock
from agent.platform.cost import cost_of
from agent.platform.model import ModelProvider

EVAL_PRINCIPAL = "eval-user"


class _EvalParker:
    def __init__(self) -> None:
        self._ids = count(1)

    async def park(self, call: ToolUseBlock, ctx: RunContext) -> PendingAction:
        return PendingAction(
            id=f"pa-{next(self._ids)}",
            session_id=ctx.session_id,
            principal_id=ctx.principal_id,
            call=call,
        )


@dataclass
class Attempt:
    case_id: str
    rep: int
    latency_s: float
    scores: list[Score] = field(default_factory=list)
    result: RunResult | None = None
    error: str | None = None
    cost_usd: float | None = None
    spans: list[RecordedSpan] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.error is None and all(s.passed for s in self.scores)


@dataclass
class CaseSummary:
    case: EvalCase
    attempts: list[Attempt]

    @property
    def pass_rate(self) -> float:
        return sum(a.passed for a in self.attempts) / len(self.attempts)

    @property
    def passed(self) -> bool:
        return self.pass_rate >= self.case.min_pass_rate


@dataclass
class Report:
    model: str
    reps: int
    cases: list[CaseSummary]
    started_at: datetime

    @property
    def attempts(self) -> list[Attempt]:
        return [a for c in self.cases for a in c.attempts]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    @property
    def total_cost_usd(self) -> float | None:
        costs = [a.cost_usd for a in self.attempts]
        return None if any(c is None for c in costs) else sum(costs)  # type: ignore[arg-type]

    def latency_percentile(self, q: int) -> float:
        latencies = sorted(a.latency_s for a in self.attempts)
        if len(latencies) == 1:
            return latencies[0]
        return statistics.quantiles(latencies, n=100, method="inclusive")[q - 1]

    def to_markdown(self) -> str:
        cost = self.total_cost_usd
        lines = [
            f"## Agent-loop eval: {'PASS' if self.passed else 'FAIL'}",
            "",
            f"Model `{self.model}` · {self.reps} reps · "
            f"{sum(c.passed for c in self.cases)}/{len(self.cases)} cases passed · "
            f"cost {'unknown' if cost is None else f'${cost:.4f}'} · "
            f"latency p50 {self.latency_percentile(50):.1f}s, "
            f"p95 {self.latency_percentile(95):.1f}s",
            "",
            "| Case | Pass rate | Needed | Result |",
            "|---|---|---|---|",
        ]
        for c in self.cases:
            lines.append(
                f"| `{c.case.id}` | {c.pass_rate:.0%} | {c.case.min_pass_rate:.0%} | "
                f"{'✓' if c.passed else '✗'} |"
            )
        failures = [a for a in self.attempts if not a.passed]
        if failures:
            lines += ["", "### Failed attempts", ""]
            for a in failures:
                lines.append(f"**`{a.case_id}` rep {a.rep}**")
                if a.error:
                    lines.append(f"- error: {a.error}")
                for s in a.scores:
                    if not s.passed:
                        lines.append(f"- ✗ {s.grader}: {s.detail}")
                if a.result is not None:
                    calls = [f"{c.name}({json.dumps(c.input)})" for c in tool_calls(a.result)]
                    lines.append(f"- tool calls: {', '.join(calls) or 'none'}")
                    lines.append(f"- stop: {a.result.stop.value} · output: {a.result.output!r}")
                lines.append("")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "reps": self.reps,
            "started_at": self.started_at.isoformat(),
            "passed": self.passed,
            "total_cost_usd": self.total_cost_usd,
            "latency_p50_s": self.latency_percentile(50),
            "latency_p95_s": self.latency_percentile(95),
            "cases": [
                {
                    "id": c.case.id,
                    "pass_rate": c.pass_rate,
                    "min_pass_rate": c.case.min_pass_rate,
                    "passed": c.passed,
                    "attempts": [
                        {
                            "rep": a.rep,
                            "passed": a.passed,
                            "latency_s": a.latency_s,
                            "cost_usd": a.cost_usd,
                            "error": a.error,
                            "stop": a.result.stop.value if a.result else None,
                            "output": a.result.output if a.result else None,
                            "tool_calls": [
                                {"name": t.name, "input": t.input} for t in tool_calls(a.result)
                            ]
                            if a.result
                            else [],
                            "scores": [vars(s) for s in a.scores],
                        }
                        for a in c.attempts
                    ],
                }
                for c in self.cases
            ],
        }


class Harness:
    def __init__(
        self, model: ModelProvider, spec: AgentSpec, clock: Clock, concurrency: int = 4
    ) -> None:
        self._model = model
        self._spec = spec
        self._clock = clock
        self._slots = asyncio.Semaphore(concurrency)

    async def run(self, cases: list[EvalCase], reps: int = 1) -> Report:
        started = datetime.now(UTC)
        attempts = await asyncio.gather(
            *(self._attempt(case, rep) for case in cases for rep in range(1, reps + 1))
        )
        summaries = [
            CaseSummary(case, [a for a in attempts if a.case_id == case.id]) for case in cases
        ]
        return Report(model=self._spec.model, reps=reps, cases=summaries, started_at=started)

    async def _attempt(self, case: EvalCase, rep: int) -> Attempt:
        async with self._slots:
            tracer = InMemoryTracer()
            registry = StaticToolRegistry([t.build() for t in case.tools])
            spec = self._spec.model_copy(update={"tool_names": [t.name for t in case.tools]})
            executor = PolicyExecutor(registry, ConfirmWrites(), _EvalParker())
            runner = TracedAgentRunner(
                ReasoningLoop(
                    model=TracedModelProvider(self._model, tracer),
                    context=InstructionsContext(self._clock),
                    tools=registry,
                    executor=TracedToolExecutor(executor, tracer, capture_content=True),
                ),
                tracer,
            )
            event = InboundEvent(
                event_id=f"{case.id}-{rep}",
                tenant_id=EVAL_PRINCIPAL,
                session_id=f"eval-{case.id}-{rep}",
                principal_id=EVAL_PRINCIPAL,
                text=case.text,
            )
            started = time.perf_counter()
            try:
                result = await runner.run(
                    RunContext.for_event(spec, event), event, list(case.history)
                )
            except Exception as e:
                return Attempt(
                    case.id,
                    rep,
                    time.perf_counter() - started,
                    error=f"{type(e).__name__}: {e}",
                    spans=tracer.spans,
                )
            return Attempt(
                case.id,
                rep,
                time.perf_counter() - started,
                scores=[g.grade(result) for g in case.graders],
                result=result,
                cost_usd=cost_of(spec.model, result.usage),
                spans=tracer.spans,
            )
