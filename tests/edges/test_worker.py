"""Tests for edges/worker.py — a reply that can't be delivered is logged, never lost silently."""

import logging

from agent.domain.agent import AgentSpec, InboundEvent
from agent.edges.channels import OutboundMessage, RoutedEvent, SendFailed
from agent.edges.worker import StaticAgentDirectory, Worker

SPEC = AgentSpec(name="bot", instructions="Be brief.", model="m")
EVENT = InboundEvent(
    event_id="wamid.E1", tenant_id="u", session_id="u", principal_id="u", text="hola"
)
ROUTED = RoutedEvent(agent_key="bot", event=EVENT)


class FakeApprovals:
    async def waiting(self, ctx):
        return None


class FakeTurns:
    async def handle(self, ctx, event):
        return object()  # the responder below ignores it


class FakeResponder:
    def reply(self, result):
        return "respuesta"

    def failure(self):
        return "falló"


class FakeReplies:
    def classify(self, text):
        return None


class FakeChannel:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[OutboundMessage] = []

    async def send(self, message: OutboundMessage) -> None:
        if self.error:
            raise self.error
        self.sent.append(message)


def worker(channel: FakeChannel) -> Worker:
    return Worker(
        StaticAgentDirectory({"bot": SPEC}),
        FakeTurns(),
        FakeApprovals(),
        FakeReplies(),
        FakeResponder(),
        channel,
    )


async def test_a_delivered_reply_is_sent_quoting_the_event():
    channel = FakeChannel()
    await worker(channel).process(ROUTED)
    assert [m.text for m in channel.sent] == ["respuesta"]
    assert channel.sent[0].reply_to_event_id == "wamid.E1"


async def test_a_refused_send_is_logged_with_the_channels_reason(caplog):
    channel = FakeChannel(SendFailed(401, "190: Error validating access token"))
    with caplog.at_level(logging.ERROR):
        await worker(channel).process(ROUTED)  # must not raise
    [record] = caplog.records
    assert "wamid.E1" in record.getMessage()
    assert "190: Error validating access token" in record.getMessage()
    assert record.exc_info is None  # the channel explained itself; no traceback noise


async def test_an_unexpected_send_error_is_logged_with_its_traceback(caplog):
    channel = FakeChannel(RuntimeError("boom"))
    with caplog.at_level(logging.ERROR):
        await worker(channel).process(ROUTED)
    [record] = caplog.records
    assert "wamid.E1" in record.getMessage()
    assert record.exc_info is not None
