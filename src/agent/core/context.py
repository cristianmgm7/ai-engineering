"""L2 · ContextBuilder — decides exactly what the model sees on each step.

The system prompt has two parts: a **stable** part (instructions, tool guidance;
cacheable across turns and sessions) and a **volatile** tail (today's date, per-turn
state) placed after the cache breakpoint. Mixing per-turn data into the stable part
breaks the cache for everyone.

The volatile part also means the system prompt differs between runs, so thinking
blocks from earlier runs can't be replayed (they're bound to their prefix). Memory
rebuilds past turns as plain text for that reason.
"""

from datetime import UTC
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agent.core.run import RunContext
from agent.platform.clock import Clock
from agent.platform.model import SystemBlock


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    stable: str
    volatile: str = ""

    def blocks(self) -> list[SystemBlock]:
        """The stable part ends with a cache breakpoint; the volatile part comes after it."""
        parts = [SystemBlock(text=self.stable, cache=True), SystemBlock(text=self.volatile)]
        return [p for p in parts if p.text]


class ContextBuilder(Protocol):
    def build(self, ctx: RunContext) -> Prompt: ...


class InstructionsContext:
    """The kernel's default ContextBuilder: the agent's instructions (stable) plus the
    current date and time (volatile). Without the date the model invents one.

    Memory and retrieved knowledge join the prompt in later components.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def build(self, ctx: RunContext) -> Prompt:
        now = self._clock.now().astimezone(UTC)
        return Prompt(
            stable=ctx.spec.instructions,
            volatile=f"Current date and time: {now:%A}, {now.day} {now:%B %Y, %H:%M} UTC.",
        )
