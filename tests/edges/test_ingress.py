"""Tests for edges/ingress.py — status codes of the front door."""

import httpx
import pytest

from agent.domain.agent import InboundEvent
from agent.edges.channels import BadRequest, InboundRequest, RoutedEvent, Unauthorized
from agent.edges.ingress import ChannelRoute, create_ingress

EVENT = InboundEvent(event_id="m1", tenant_id="u1", session_id="c1", principal_id="u1", text="hi")


class FakeAdapter:
    def __init__(self, outcome, challenge=None):
        self.outcome = outcome
        self.challenge = challenge
        self.seen: list[InboundRequest] = []

    def parse_inbound(self, request: InboundRequest):
        self.seen.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def verify(self, request: InboundRequest):
        self.seen.append(request)
        if isinstance(self.challenge, Exception):
            raise self.challenge
        return self.challenge

    async def send(self, message) -> None:
        raise AssertionError("ingress never sends")


class FakeQueue:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.items: list[RoutedEvent] = []

    async def enqueue(self, routed: RoutedEvent) -> None:
        if self.fail:
            raise ConnectionError("queue down")
        self.items.append(routed)


def client(adapter, queue, agents=("agent",)):
    app = create_ingress({"chat": ChannelRoute(adapter, queue)}, set(agents))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def post(adapter, queue, agents=("agent",), path="/webhooks/chat/agent"):
    async with client(adapter, queue, agents) as c:
        return await c.post(path, content=b'{"x":1}', headers={"X-Signature": "abc"})


async def get(adapter, path="/webhooks/chat/agent", params=None):
    async with client(adapter, FakeQueue()) as c:
        return await c.get(path, params=params or {})


async def test_accepted_events_are_enqueued_with_the_raw_request():
    adapter, queue = FakeAdapter(RoutedEvent(agent_key="agent", event=EVENT)), FakeQueue()
    response = await post(adapter, queue)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert queue.items[0].event == EVENT
    seen = adapter.seen[0]
    assert seen.body == b'{"x":1}'
    assert seen.headers["x-signature"] == "abc" and seen.path == {"hook": "agent"}


@pytest.mark.parametrize(
    ("outcome", "status"), [(Unauthorized("bad signature"), 401), (BadRequest("bad json"), 400)]
)
async def test_rejections_keep_their_status_and_enqueue_nothing(outcome, status):
    queue = FakeQueue()
    response = await post(FakeAdapter(outcome), queue)
    assert response.status_code == status and queue.items == []


async def test_ignored_events_are_acknowledged():
    queue = FakeQueue()
    response = await post(FakeAdapter(None), queue)
    assert response.status_code == 200 and response.json()["ignored"] is True
    assert queue.items == []


async def test_unknown_agent_and_unknown_channel_are_404():
    routed = RoutedEvent(agent_key="someone-else", event=EVENT)
    assert (await post(FakeAdapter(routed), FakeQueue())).status_code == 404
    other = await post(FakeAdapter(routed), FakeQueue(), path="/webhooks/slack/agent")
    assert other.status_code == 404


async def test_a_queue_failure_is_a_503_so_the_channel_retries():
    routed = RoutedEvent(agent_key="agent", event=EVENT)
    assert (await post(FakeAdapter(routed), FakeQueue(fail=True))).status_code == 503


# --- The subscription handshake (GET) --------------------------------------------


async def test_handshake_echoes_the_challenge_as_plain_text():
    adapter = FakeAdapter(None, challenge="162534")
    response = await get(adapter, params={"hub.mode": "subscribe", "hub.challenge": "162534"})
    assert response.status_code == 200 and response.text == "162534"
    assert "text/plain" in response.headers["content-type"]
    assert adapter.seen[0].query["hub.mode"] == "subscribe"
    assert adapter.seen[0].path == {"hook": "agent"}


async def test_handshake_rejections_keep_their_status():
    adapter = FakeAdapter(None, challenge=Unauthorized("bad verify token"))
    assert (await get(adapter)).status_code == 401


async def test_handshake_is_404_for_channels_without_one_and_unknown_channels():
    assert (await get(FakeAdapter(None, challenge=None))).status_code == 404
    other = await get(FakeAdapter(None, challenge="x"), path="/webhooks/slack/agent")
    assert other.status_code == 404
