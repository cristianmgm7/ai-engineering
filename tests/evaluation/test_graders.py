"""Tests for evaluation/graders.py."""

from agent.core.run import RunResult, RunStop
from agent.domain.agent import Message, Role, TextBlock, ToolUseBlock
from agent.evaluation.graders import (
    CalledTool,
    MaxSteps,
    MaxWords,
    NotCalledTool,
    OutputContains,
    OutputExcludes,
    Stopped,
)
from agent.platform.model import Usage


def run(output: str = "", calls: list[ToolUseBlock] = (), stop=RunStop.COMPLETED, steps=1):
    messages = [Message.text(Role.USER, "hi")]
    if calls:
        messages.append(Message(role=Role.ASSISTANT, content=list(calls)))
    messages.append(Message(role=Role.ASSISTANT, content=[TextBlock(text=output)]))
    return RunResult(stop=stop, output=output, new_messages=messages, usage=Usage(), steps=steps)


CREATE = ToolUseBlock(
    id="1", name="calendar__create", input={"title": "Lunch with Ana", "start": "2026-10-07T13:00"}
)


def test_called_tool_matches_string_args_by_case_insensitive_substring():
    result = run(calls=[CREATE])
    assert CalledTool("calendar__create", {"title": "lunch"}).grade(result).passed
    miss = CalledTool("calendar__create", {"start": "2026-10-08"}).grade(result)
    assert not miss.passed and "2026-10-07" in miss.detail


def test_called_tool_explains_what_was_called_instead():
    score = CalledTool("calendar__list").grade(run(calls=[CREATE]))
    assert not score.passed and "calendar__create" in score.detail


def test_not_called_tool_for_one_tool_or_any():
    result = run(calls=[CREATE])
    assert NotCalledTool("calendar__list").grade(result).passed
    assert not NotCalledTool("calendar__create").grade(result).passed
    assert not NotCalledTool().grade(result).passed
    assert NotCalledTool().grade(run("hi")).passed


def test_stopped():
    assert Stopped(RunStop.COMPLETED).grade(run()).passed
    score = Stopped(RunStop.AWAITING_APPROVAL).grade(run())
    assert not score.passed and "completed" in score.detail


def test_output_contains_all_or_any():
    result = run("You have Standup and Lunch.")
    assert OutputContains(("standup", "lunch")).grade(result).passed
    assert not OutputContains(("standup", "dinner")).grade(result).passed
    assert OutputContains(("standup", "dinner"), any_of=True).grade(result).passed


def test_output_excludes():
    assert OutputExcludes(("?",)).grade(run("Done.")).passed
    assert not OutputExcludes(("?",)).grade(run("Should I book it?")).passed


def test_max_words_and_steps():
    assert MaxWords(3).grade(run("one two three")).passed
    assert not MaxWords(2).grade(run("one two three")).passed
    assert not MaxSteps(1).grade(run(steps=2)).passed
