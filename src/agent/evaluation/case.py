"""EvalCase and ScriptedTool — a scenario to run, with tools whose answers are fixed.

Scripted tools go through the real ``PolicyExecutor``, so a WRITE tool is parked
exactly like production (same ``parked_result``). Only their output is scripted.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.core.run import RunContext
from agent.core.tools import FunctionTool, ToolResult
from agent.domain.agent import Effect, Message
from agent.evaluation.graders import Grader


class AnyArgs(BaseModel):
    """Accepts whatever the model sends; graders check the arguments instead."""

    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True)
class ScriptedTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    effect: Effect = Effect.READ
    returns: str = "ok"
    is_error: bool = False

    def build(self) -> FunctionTool:
        result = ToolResult(content=self.returns, is_error=self.is_error)

        async def handler(args: AnyArgs, ctx: RunContext) -> ToolResult:
            return result

        return FunctionTool(
            self.name, self.description, AnyArgs, handler, self.effect, self.input_schema
        )


@dataclass(frozen=True)
class EvalCase:
    id: str
    description: str
    text: str  # what the user says
    graders: list[Grader]
    tools: list[ScriptedTool] = field(default_factory=list)
    history: list[Message] = field(default_factory=list)
    min_pass_rate: float = 1.0  # share of reps that must pass for the case to pass
