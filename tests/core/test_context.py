"""Tests for core/context.py — the default ContextBuilder."""

from datetime import UTC, datetime, timedelta, timezone

from agent.core.context import InstructionsContext, Prompt
from agent.core.run import RunContext
from agent.domain.agent import AgentSpec
from agent.platform.clock import FixedClock
from agent.platform.model import SystemBlock

SPEC = AgentSpec(name="a", instructions="Be brief.", model="m")
CTX = RunContext(spec=SPEC, tenant_id="t", session_id="s", principal_id="p")


def test_instructions_are_stable_and_the_date_is_volatile():
    clock = FixedClock(datetime(2026, 10, 6, 14, 5, tzinfo=UTC))
    prompt = InstructionsContext(clock).build(CTX)
    assert prompt == Prompt(
        stable="Be brief.", volatile="Current date and time: Tuesday, 6 October 2026, 14:05 UTC."
    )


def test_the_date_is_rendered_in_utc():
    bogota = timezone(timedelta(hours=-5))
    clock = FixedClock(datetime(2026, 10, 6, 21, 30, tzinfo=bogota))
    assert "Wednesday, 7 October 2026, 02:30 UTC" in InstructionsContext(clock).build(CTX).volatile


def test_blocks_put_the_breakpoint_after_the_stable_part_and_skip_empty_parts():
    assert Prompt(stable="S", volatile="V").blocks() == [
        SystemBlock(text="S", cache=True),
        SystemBlock(text="V"),
    ]
    assert Prompt(stable="", volatile="V").blocks() == [SystemBlock(text="V")]


def test_only_the_volatile_part_changes_between_days():
    day1 = InstructionsContext(FixedClock(datetime(2026, 10, 6, tzinfo=UTC))).build(CTX)
    day2 = InstructionsContext(FixedClock(datetime(2026, 10, 7, tzinfo=UTC))).build(CTX)
    assert day1.stable == day2.stable and day1.volatile != day2.volatile
