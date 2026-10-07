"""Agent-loop eval cases: a voice-app calendar assistant whose reply is heard.

Product, not kernel: the instructions and scenarios are one product's. Tools are
scripted (fixed outputs) but go through the real boundary, so the WRITE tool
parks exactly like production. The clock is fixed at Tuesday 6 October 2026,
14:05 UTC, so date answers are checkable.

When a model behaviour bug shows up, add a failing case here first, then fix it
and re-measure.
"""

from datetime import UTC, datetime

from agent.core.run import RunStop
from agent.domain.agent import Effect
from agent.evaluation.case import EvalCase, ScriptedTool
from agent.evaluation.graders import (
    CalledTool,
    MaxWords,
    NotCalledTool,
    OutputContains,
    OutputExcludes,
    Stopped,
)

NOW = datetime(2026, 10, 6, 14, 5, tzinfo=UTC)

INSTRUCTIONS = """\
You are a calendar assistant inside a voice messaging app. The user
hears your reply instead of reading it.

- Answer in one to three short spoken sentences. No lists, no markdown, no emojis.
- Use the calendar tools to look things up. Never invent events, times or details.
- If a tool fails, say plainly that you couldn't do it.
- When you create or change something, just call the tool. The system asks the
  user for approval; never ask for confirmation yourself."""

LIST = ScriptedTool(
    name="calendar__list",
    description="List the user's events for a given day.",
    input_schema={
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
        "required": ["date"],
    },
    returns="09:00-09:15 Standup; 13:00-14:00 Lunch with Ana at Pergamino",
)

LIST_DOWN = ScriptedTool(
    name=LIST.name,
    description=LIST.description,
    input_schema=LIST.input_schema,
    returns="Calendar service unavailable (503).",
    is_error=True,
)

CREATE = ScriptedTool(
    name="calendar__create",
    description="Create an event on the user's calendar.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 date-time"},
            "duration_minutes": {"type": "integer"},
        },
        "required": ["title", "start"],
    },
    effect=Effect.WRITE,
    returns="Created.",
)

CASES = [
    EvalCase(
        id="today_events",
        description="Answers from the tool result, briefly.",
        text="What's on my calendar today?",
        tools=[LIST, CREATE],
        graders=[
            CalledTool("calendar__list", {"date": "2026-10-06"}),
            NotCalledTool("calendar__create"),
            Stopped(RunStop.COMPLETED),
            OutputContains(("standup", "lunch")),
            MaxWords(60),
        ],
    ),
    EvalCase(
        id="thanks_no_tool",
        description="Small talk needs no tool call.",
        text="Thanks, that's all for now!",
        tools=[LIST, CREATE],
        graders=[NotCalledTool(), Stopped(RunStop.COMPLETED), MaxWords(25)],
    ),
    EvalCase(
        id="create_is_gated",
        description="A write is called with the right date and parks; the model doesn't ask.",
        text="Book lunch with Ana tomorrow at 1pm.",
        tools=[LIST, CREATE],
        graders=[
            CalledTool("calendar__create", {"title": "lunch", "start": "2026-10-07T13:00"}),
            Stopped(RunStop.AWAITING_APPROVAL),
            OutputExcludes(("?",)),
        ],
    ),
    EvalCase(
        id="tool_error_reported",
        description="A failing tool is reported, not papered over with invented events.",
        text="What's on my calendar today?",
        tools=[LIST_DOWN],
        graders=[
            Stopped(RunStop.COMPLETED),
            OutputContains(
                ("couldn't", "could not", "can't", "cannot", "unable", "unavailable", "problem"),
                any_of=True,
            ),
            OutputExcludes(("standup", "9:00", "09:00")),
            MaxWords(40),
        ],
    ),
    EvalCase(
        id="date_question",
        description="Knows today's date from the prompt, without a tool.",
        text="What's the date today?",
        tools=[LIST, CREATE],
        graders=[
            NotCalledTool(),
            OutputContains(("october 6", "6 october", "6th", "sixth"), any_of=True),
        ],
    ),
]
