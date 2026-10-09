"""Tests for edges/whatsapp/adapter.py — handshake, signature, mapping, outbound."""

import hashlib
import hmac
import json

import httpx
import pytest

from agent.edges.channels import (
    BadRequest,
    InboundRequest,
    OutboundMessage,
    SendFailed,
    Unauthorized,
)
from agent.edges.whatsapp.adapter import WhatsAppAdapter

VERIFY_TOKEN = "verify-me"
APP_SECRET = "app-secret"
HOOK = "pn-1"  # the bot's phone number id
WA_ID = "5215550001111"  # the customer

TEXT_MESSAGE = {"from": WA_ID, "id": "wamid.X1", "type": "text", "text": {"body": "hola"}}


def adapter(**kwargs) -> WhatsAppAdapter:
    defaults = {"verify_token": VERIFY_TOKEN, "app_secret": APP_SECRET}
    return WhatsAppAdapter(**{**defaults, **kwargs})


def meta_payload(messages=None, statuses=None, phone_number_id=HOOK) -> dict:
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_number_id}}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": value}]}],
    }


def signed(payload: dict, secret: str = APP_SECRET) -> InboundRequest:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return InboundRequest(
        body=body, headers={"x-hub-signature-256": signature}, path={"hook": HOOK}
    )


def handshake(**query) -> InboundRequest:
    defaults = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"}
    return InboundRequest(body=b"", headers={}, path={"hook": HOOK}, query={**defaults, **query})


# --- handshake --------------------------------------------------------------------


def test_handshake_echoes_the_challenge():
    assert adapter().verify(handshake()) == "42"


def test_handshake_rejects_a_bad_token():
    with pytest.raises(Unauthorized):
        adapter().verify(handshake(**{"hub.verify_token": "guess"}))


def test_handshake_fails_closed_without_a_configured_token():
    with pytest.raises(Unauthorized):
        adapter(verify_token=None).verify(handshake())


def test_handshake_rejects_a_wrong_mode():
    with pytest.raises(BadRequest):
        adapter().verify(handshake(**{"hub.mode": "unsubscribe"}))


# --- inbound ------------------------------------------------------------------------


def test_a_text_message_maps_onto_the_standard_names():
    routed = adapter().parse_inbound(signed(meta_payload(messages=[TEXT_MESSAGE])))
    assert routed is not None and routed.agent_key == HOOK
    event = routed.event
    assert event.event_id == "wamid.X1"
    assert event.tenant_id == event.principal_id == event.session_id == WA_ID
    assert event.text == "hola"


def test_a_bad_or_missing_signature_is_rejected():
    good = signed(meta_payload(messages=[TEXT_MESSAGE]))
    with pytest.raises(Unauthorized):
        adapter().parse_inbound(signed(meta_payload(messages=[TEXT_MESSAGE]), secret="wrong"))
    with pytest.raises(Unauthorized):
        adapter().parse_inbound(InboundRequest(body=good.body, headers={}, path={"hook": HOOK}))


def test_fails_closed_without_a_configured_secret():
    with pytest.raises(Unauthorized):
        adapter(app_secret=None).parse_inbound(signed(meta_payload(messages=[TEXT_MESSAGE])))


def test_statuses_and_other_numbers_are_ignored():
    delivered = {"id": "wamid.X1", "status": "delivered", "recipient_id": WA_ID}
    assert adapter().parse_inbound(signed(meta_payload(statuses=[delivered]))) is None
    other = meta_payload(messages=[TEXT_MESSAGE], phone_number_id="pn-2")
    assert adapter().parse_inbound(signed(other)) is None


def test_non_text_messages_are_ignored_for_now():
    audio = {"from": WA_ID, "id": "wamid.X2", "type": "audio", "audio": {"id": "media-1"}}
    assert adapter().parse_inbound(signed(meta_payload(messages=[audio]))) is None


def test_malformed_payloads_are_bad_requests():
    body = b"not json"
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    request = InboundRequest(
        body=body, headers={"x-hub-signature-256": signature}, path={"hook": HOOK}
    )
    with pytest.raises(BadRequest):
        adapter().parse_inbound(request)
    with pytest.raises(BadRequest):
        adapter().parse_inbound(signed({"object": "whatsapp_business_account"}))


# --- outbound -----------------------------------------------------------------------


async def test_send_posts_to_the_graph_api_and_quotes_the_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = adapter(http=http, access_token="token-1", api_base_url="https://graph.test")
    await out.send(
        OutboundMessage(agent_key=HOOK, session_id=WA_ID, text="hola", reply_to_event_id="wamid.X1")
    )
    assert seen["url"] == f"https://graph.test/v21.0/{HOOK}/messages"
    assert seen["auth"] == "Bearer token-1"
    assert seen["json"]["messaging_product"] == "whatsapp"
    assert seen["json"]["to"] == WA_ID
    assert seen["json"]["text"] == {"body": "hola"}
    assert seen["json"]["context"] == {"message_id": "wamid.X1"}


async def test_send_without_configuration_raises():
    with pytest.raises(RuntimeError):
        await adapter().send(OutboundMessage(agent_key=HOOK, session_id=WA_ID, text="hola"))


async def test_a_refused_send_raises_with_metas_own_explanation():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": 190, "message": "Error validating access token"}}
        return httpx.Response(401, json=body)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = adapter(http=http, access_token="expired", api_base_url="https://graph.test")
    with pytest.raises(SendFailed) as failure:
        await out.send(OutboundMessage(agent_key=HOOK, session_id=WA_ID, text="hola"))
    assert failure.value.status_code == 401
    assert "190" in failure.value.detail and "access token" in failure.value.detail


async def test_a_refused_send_without_a_json_body_still_explains_itself():
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad gateway"))
    )
    out = adapter(http=http, access_token="t", api_base_url="https://graph.test")
    with pytest.raises(SendFailed) as failure:
        await out.send(OutboundMessage(agent_key=HOOK, session_id=WA_ID, text="hola"))
    assert failure.value.detail == "bad gateway"
