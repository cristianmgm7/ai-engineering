"""Tests for the standard message vocabulary (platform/model.py) and agent entities
(domain/agent.py)."""

from datetime import datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from agent.domain.agent import (
    AgentSpec,
    ContentBlock,
    Effect,
    InboundEvent,
    Message,
    Role,
    RunLimits,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
)
from agent.platform.model import Usage


def test_inbound_event_fields():
    event = InboundEvent(
        event_id="m-1", tenant_id="user-1", session_id="chan-1", principal_id="user-1", text="hola"
    )
    assert event.session_id == "chan-1"
    assert event.text == "hola"


def test_role_is_string_valued():
    assert Role.USER == "user"
    assert Role.ASSISTANT == "assistant"


def test_message_text_helper_and_timestamp():
    msg = Message.text(Role.USER, "hola", event_id="m-1")
    assert msg.plain_text() == "hola"
    assert msg.event_id == "m-1"
    assert isinstance(msg.created_at, datetime)
    assert msg.created_at.tzinfo is not None


def test_plain_text_skips_tool_blocks():
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="Let me check. "),
            ToolUseBlock(id="t1", name="calendar__list", input={}),
            TextBlock(text="Done."),
        ],
    )
    assert msg.plain_text() == "Let me check. Done."
    assert [t.name for t in msg.tool_uses()] == ["calendar__list"]


def test_content_blocks_parse_by_discriminator():
    block = TypeAdapter(ContentBlock).validate_python(
        {"type": "tool_use", "id": "t1", "name": "x", "input": {"a": 1}}
    )
    assert isinstance(block, ToolUseBlock)


def test_models_are_frozen():
    msg = Message.text(Role.ASSISTANT, "hi")
    with pytest.raises(ValidationError):
        msg.role = Role.USER


def test_invalid_role_rejected():
    with pytest.raises(ValidationError):
        Message.text("system", "x")


def test_agent_spec_defaults_limits():
    spec = AgentSpec(name="calendar", instructions="Help with the calendar.", model="m")
    assert spec.limits == RunLimits()
    assert spec.tool_names == []


def test_run_limits_reject_zero_steps():
    with pytest.raises(ValidationError):
        RunLimits(max_steps=0)


def test_tool_spec_effect_defaults_to_read():
    spec = ToolSpec(name="t", description="d", input_schema={"type": "object"})
    assert spec.effect is Effect.READ


def test_usage_adds_up():
    total = Usage(input_tokens=10, output_tokens=2) + Usage(input_tokens=5, cache_read_tokens=7)
    assert total == Usage(input_tokens=15, output_tokens=2, cache_read_tokens=7)
