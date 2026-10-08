"""Tests for edges/whatsapp/app.py — the whole edge, end to end with fakes.

A signed Meta webhook goes in; a fake model answers; the reply must leave
through the Graph API. Only the model and the HTTP transport are fake.
"""

import hashlib
import hmac
import json

import httpx

from agent.edges.whatsapp.app import build
from agent.platform.config import Settings
from agent.platform.model import (
    Message,
    ModelRequest,
    ModelResponse,
    Role,
    StopReason,
    TextBlock,
    Usage,
)

HOOK = "pn-1"
APP_SECRET = "app-secret"
WA_ID = "5215550001111"


class OneLinerModel:
    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            message=Message(role=Role.ASSISTANT, content=[TextBlock(text="Hola, aquí estoy.")]),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=5, output_tokens=5),
            model="fake",
        )


def settings() -> Settings:
    return Settings(
        _env_file=None,
        anthropic_api_key="sk-ant-test",
        whatsapp_verify_token="verify-me",
        whatsapp_app_secret=APP_SECRET,
        whatsapp_access_token="token-1",
        whatsapp_phone_number_id=HOOK,
    )


def graph_client(sent: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def signed_body() -> tuple[bytes, dict]:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": HOOK},
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": "wamid.X1",
                                    "type": "text",
                                    "text": {"body": "hola"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": signature}


async def test_a_signed_webhook_becomes_a_graph_reply():
    sent: list[httpx.Request] = []
    app = build(settings(), model=OneLinerModel(), http=graph_client(sent))
    body, headers = signed_body()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.http_app), base_url="http://test"
    ) as client:
        response = await client.post(f"/webhooks/whatsapp/{HOOK}", content=body, headers=headers)
    assert response.status_code == 200
    await app.queue.drain()

    assert len(sent) == 1
    assert str(sent[0].url).endswith(f"/v21.0/{HOOK}/messages")
    assert sent[0].headers["authorization"] == "Bearer token-1"
    out = json.loads(sent[0].content)
    assert out["to"] == WA_ID
    assert out["text"]["body"] == "Hola, aquí estoy."
    assert out["context"] == {"message_id": "wamid.X1"}  # the reply quotes the question


async def test_the_handshake_works_through_the_whole_app():
    app = build(settings(), model=OneLinerModel(), http=graph_client([]))
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "777"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.http_app), base_url="http://test"
    ) as client:
        ok = await client.get(f"/webhooks/whatsapp/{HOOK}", params=params)
        bad = await client.get(
            f"/webhooks/whatsapp/{HOOK}", params={**params, "hub.verify_token": "guess"}
        )
    assert ok.status_code == 200 and ok.text == "777"
    assert bad.status_code == 401


async def test_unconfigured_app_fails_closed():
    bare = Settings(_env_file=None, anthropic_api_key="sk-ant-test")
    app = build(bare, model=OneLinerModel(), http=graph_client([]))
    body, headers = signed_body()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.http_app), base_url="http://test"
    ) as client:
        response = await client.post(f"/webhooks/whatsapp/{HOOK}", content=body, headers=headers)
    assert response.status_code == 401
