"""Run the agent-loop eval suite against the real model.

    uv run python evals/agent_loop/run.py --reps 3 [--case today_events,thanks_no_tool]

Prints a markdown report, writes JSON to evals/results/, and exits non-zero when
any case is below its pass rate (the gate). Needs ANTHROPIC_API_KEY in .env, and
costs real money: roughly cases × reps × 2 model calls.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from cases import CASES, INSTRUCTIONS, NOW

from agent.adapters.models.anthropic import AnthropicModelProvider
from agent.domain.agent import AgentSpec, RunLimits
from agent.evaluation.harness import Harness
from agent.platform.clock import FixedClock
from agent.platform.config import get_settings

RESULTS = Path(__file__).resolve().parents[1] / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--case", help="comma-separated case ids (default: all)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    cases = CASES
    if args.case:
        wanted = set(args.case.split(","))
        unknown = wanted - {c.id for c in CASES}
        if unknown:
            parser.error(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in CASES if c.id in wanted]

    settings = get_settings()
    spec = AgentSpec(
        name="cv-calendar-assistant",
        instructions=INSTRUCTIONS,
        model=settings.anthropic_model,
        limits=RunLimits(max_steps=settings.agent_max_steps, max_tokens=settings.agent_max_tokens),
    )
    harness = Harness(
        AnthropicModelProvider.from_settings(settings),
        spec,
        FixedClock(NOW),
        concurrency=args.concurrency,
    )
    report = asyncio.run(harness.run(cases, reps=args.reps))

    print(report.to_markdown())
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"agent-loop-{report.started_at:%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps(report.to_json(), indent=2))
    print(f"\nWrote {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
