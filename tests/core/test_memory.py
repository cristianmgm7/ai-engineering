"""Tests for core/memory.py and the in-memory session store."""

from agent.adapters.stores.memory import InMemorySessionStore
from agent.core.memory import WindowMemory, text_window
from agent.core.run import RunContext
from agent.domain.agent import (
    AgentSpec,
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.platform.model import OpaqueBlock

THINKING = OpaqueBlock(provider="anthropic", raw={"type": "thinking", "signature": "s"})


def user(text: str, event_id: str) -> Message:
    return Message.text(Role.USER, text, event_id=event_id)


def tool_round(text: str = "") -> list[Message]:
    blocks = [THINKING, *([TextBlock(text=text)] if text else [])]
    return [
        Message(
            role=Role.ASSISTANT,
            content=[*blocks, ToolUseBlock(id="t", name="cal", input={})],
        ),
        Message(role=Role.USER, content=[ToolResultBlock(tool_use_id="t", content="2 events")]),
    ]


def answer(text: str) -> Message:
    return Message(role=Role.ASSISTANT, content=[THINKING, TextBlock(text=text)])


def as_pairs(messages: list[Message]) -> list[tuple[str, str]]:
    return [(m.role.value, m.plain_text()) for m in messages]


def test_a_tool_round_becomes_the_question_and_the_final_answer():
    transcript = [user("today?", "e1"), *tool_round("Checking."), answer("You have 2 events.")]
    window = text_window(transcript, max_turns=10)
    assert as_pairs(window) == [("user", "today?"), ("assistant", "You have 2 events.")]
    assert all(isinstance(b, TextBlock) for m in window for b in m.content)  # no thinking/tools
    assert window[0].event_id == "e1"
    assert window[1].created_at == transcript[-1].created_at


def test_only_the_last_turns_are_kept():
    transcript = []
    for i in range(1, 5):
        transcript += [user(f"q{i}", f"e{i}"), answer(f"a{i}")]
    assert as_pairs(text_window(transcript, max_turns=2)) == [
        ("user", "q3"),
        ("assistant", "a3"),
        ("user", "q4"),
        ("assistant", "a4"),
    ]
    assert text_window(transcript, max_turns=0) == []


def test_a_turn_that_parked_without_text_keeps_only_the_question():
    transcript = [user("book lunch", "e1"), *tool_round(), user("thanks", "e2"), answer("Sure.")]
    assert as_pairs(text_window(transcript, max_turns=10)) == [
        ("user", "book lunch"),
        ("user", "thanks"),
        ("assistant", "Sure."),
    ]


def test_messages_before_the_first_inbound_message_are_dropped():
    orphan = Message(role=Role.USER, content=[ToolResultBlock(tool_use_id="x", content="?")])
    transcript = [orphan, answer("stray"), user("hi", "e1"), answer("Hello.")]
    assert as_pairs(text_window(transcript, max_turns=10)) == [
        ("user", "hi"),
        ("assistant", "Hello."),
    ]


async def test_store_is_append_only_per_session_and_knows_its_events():
    store = InMemorySessionStore()
    await store.append("s1", [user("a", "e1")])
    await store.append("s1", [answer("b")])
    await store.append("s2", [user("other", "e9")])

    assert as_pairs(await store.load("s1")) == [("user", "a"), ("assistant", "b")]
    assert await store.has_event("s1", "e1") and not await store.has_event("s1", "e9")
    loaded = await store.load("s1")
    loaded.clear()  # callers get a copy
    assert len(await store.load("s1")) == 2


async def test_window_memory_saves_verbatim_and_returns_the_text_window():
    store = InMemorySessionStore()
    memory = WindowMemory(store, max_turns=5)
    ctx = RunContext(
        spec=AgentSpec(name="a", instructions="", model="m"),
        tenant_id="u",
        session_id="s1",
        principal_id="u",
    )
    transcript = [user("today?", "e1"), *tool_round(), answer("2 events.")]
    await memory.save(ctx, transcript)

    assert await store.load("s1") == transcript  # stored verbatim, thinking included
    assert as_pairs(await memory.history(ctx)) == [("user", "today?"), ("assistant", "2 events.")]
    assert await memory.seen(ctx, "e1") and not await memory.seen(ctx, "e2")
