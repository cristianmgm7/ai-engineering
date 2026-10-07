"""L2 · Memory — what the model remembers of a session (tb-agent doc 18).

The ``SessionStore`` keeps every message, append-only and verbatim. ``WindowMemory``
decides what the next run sees: the last ``max_turns`` turns, **rebuilt as plain
text** (the user's words and the agent's final answer). Tool calls, tool results
and thinking blocks from earlier runs are left out:

- Thinking blocks are bound to the exact prompt that produced them, and the system
  prompt changes between runs (it carries today's date). Replaying one in a later
  run would be rejected, so earlier runs' blocks are never sent again.
- Old tool payloads cost tokens and go stale. What the agent concluded from them
  is in its answer.

Within a single run the loop keeps the full transcript, append-only, so the tool
round itself is always replayed exactly.

Long-term memory (distilled facts that outlive the window) is a later component.
"""

from typing import Protocol

from agent.core.run import RunContext
from agent.domain.agent import Message, Role, TextBlock


class SessionStore(Protocol):
    async def load(self, session_id: str) -> list[Message]: ...

    async def append(self, session_id: str, messages: list[Message]) -> None: ...

    async def has_event(self, session_id: str, event_id: str) -> bool: ...


class Memory(Protocol):
    async def seen(self, ctx: RunContext, event_id: str) -> bool: ...

    async def history(self, ctx: RunContext) -> list[Message]: ...

    async def save(self, ctx: RunContext, messages: list[Message]) -> None: ...


class WindowMemory:
    def __init__(self, store: SessionStore, max_turns: int = 10) -> None:
        self._store = store
        self._max_turns = max_turns

    async def seen(self, ctx: RunContext, event_id: str) -> bool:
        return await self._store.has_event(ctx.session_id, event_id)

    async def history(self, ctx: RunContext) -> list[Message]:
        return text_window(await self._store.load(ctx.session_id), self._max_turns)

    async def save(self, ctx: RunContext, messages: list[Message]) -> None:
        await self._store.append(ctx.session_id, messages)


def text_window(messages: list[Message], max_turns: int) -> list[Message]:
    """The last ``max_turns`` turns as plain text.

    A turn starts at an inbound user message (one with an ``event_id``) and runs until
    the next one. It becomes the user's text plus the agent's last text, if it said
    anything (a run that parked for approval may have said nothing).
    """
    turns: list[list[Message]] = []
    for m in messages:
        if m.role is Role.USER and m.event_id is not None:
            turns.append([m])
        elif turns:  # anything before the first inbound message is dropped
            turns[-1].append(m)

    window: list[Message] = []
    for turn in turns[-max_turns:] if max_turns > 0 else []:
        user = turn[0]
        window.append(_text(user, user.plain_text()))
        answer = next(
            (m for m in reversed(turn) if m.role is Role.ASSISTANT and m.plain_text()), None
        )
        if answer is not None:
            window.append(_text(answer, answer.plain_text()))
    return window


def _text(original: Message, text: str) -> Message:
    return Message(
        role=original.role,
        content=[TextBlock(text=text)],
        event_id=original.event_id,
        created_at=original.created_at,
    )
