"""Tests for evaluation/harness.py — with a scripted model, no network."""

import json
from datetime import UTC, datetime

from agent.core.run import RunStop
from agent.domain.agent import AgentSpec, Effect, Message, Role, TextBlock, ToolUseBlock
from agent.evaluation.case import EvalCase, ScriptedTool
from agent.evaluation.graders import CalledTool, OutputContains, Stopped
from agent.evaluation.harness import Harness
from agent.platform.clock import FixedClock
from agent.platform.model import ModelRequest, ModelResponse, StopReason, Usage

SPEC = AgentSpec(name="eval", instructions="Be brief.", model="claude-sonnet-5")
CLOCK = FixedClock(datetime(2026, 10, 6, 14, 5, tzinfo=UTC))
LIST = ScriptedTool("calendar__list", "List events.", returns="09:00 Standup")
CREATE = ScriptedTool("calendar__create", "Create an event.", effect=Effect.WRITE)


def reply(*blocks, stop=StopReason.END_TURN) -> ModelResponse:
    return ModelResponse(
        message=Message(role=Role.ASSISTANT, content=list(blocks)),
        stop_reason=stop,
        usage=Usage(input_tokens=100, output_tokens=10),
        model="claude-sonnet-5",
    )


class ByText:
    """Answers per user text: calls the tool named in the script, then replies."""

    def __init__(self, script: dict[str, tuple[str | None, str]]):
        self.script = script
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        text = request.messages[0].plain_text()
        tool, answer = self.script[text]
        last = request.messages[-1]
        if tool and not any(b.type == "tool_result" for b in last.content):
            return reply(
                ToolUseBlock(id="t1", name=tool, input={"title": "Lunch"}), stop=StopReason.TOOL_USE
            )
        if text == "boom":
            raise RuntimeError("model exploded")
        return reply(TextBlock(text=answer))


CASES = [
    EvalCase(
        "reads",
        "reads then answers",
        "today?",
        tools=[LIST, CREATE],
        graders=[CalledTool("calendar__list"), OutputContains(("standup",))],
    ),
    EvalCase(
        "gated",
        "write parks",
        "book",
        tools=[LIST, CREATE],
        graders=[CalledTool("calendar__create"), Stopped(RunStop.AWAITING_APPROVAL)],
    ),
    EvalCase("wrong", "fails a grader", "hello", graders=[OutputContains(("standup",))]),
    EvalCase("crash", "model raises", "boom", graders=[]),
]
MODEL = {
    "today?": ("calendar__list", "You have Standup at 9."),
    "book": ("calendar__create", "unused"),
    "hello": (None, "Hi there."),
    "boom": (None, ""),
}


async def test_report_aggregates_cases_reps_cost_and_failures():
    model = ByText(MODEL)
    report = await Harness(model, SPEC, CLOCK).run(CASES, reps=2)

    by_id = {c.case.id: c for c in report.cases}
    assert by_id["reads"].pass_rate == 1.0 and by_id["reads"].passed
    assert by_id["gated"].passed  # parked through the real PolicyExecutor + ConfirmWrites
    assert by_id["wrong"].pass_rate == 0.0 and not by_id["wrong"].passed
    assert by_id["crash"].attempts[0].error == "RuntimeError: model exploded"
    assert not report.passed
    assert len(report.attempts) == 8

    reads = by_id["reads"].attempts[0]
    assert reads.cost_usd is not None and reads.cost_usd > 0
    assert [s.name for s in reads.spans] == [
        "agent.run",
        "model.generate",
        "tool.execute",
        "model.generate",
    ]

    md = report.to_markdown()
    assert "FAIL" in md and "`wrong`" in md and "output contains ['standup']" in md
    assert json.loads(json.dumps(report.to_json()))["cases"][0]["id"] == "reads"


async def test_prompt_carries_instructions_and_the_fixed_date():
    model = ByText(MODEL)
    await Harness(model, SPEC, CLOCK).run([CASES[2]], reps=1)
    assert model.requests[0].system_text().startswith("Be brief.")
    assert "Tuesday, 6 October 2026" in model.requests[0].system_text()


async def test_each_case_only_offers_its_own_tools():
    model = ByText(MODEL)
    await Harness(model, SPEC, CLOCK).run([CASES[2]], reps=1)
    assert model.requests[0].tools == []


async def test_min_pass_rate_lets_a_flaky_case_pass():
    flaky = EvalCase("flaky", "", "hello", graders=[OutputContains(("hi",))], min_pass_rate=0.5)
    report = await Harness(ByText(MODEL), SPEC, CLOCK).run([flaky], reps=2)
    assert report.passed
