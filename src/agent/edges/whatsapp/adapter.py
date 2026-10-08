"""WhatsApp ``ChannelAdapter`` — Meta's Cloud API webhook in, Graph API out.

Inbound is the ``messages`` field of a WhatsApp Business webhook:
``entry[].changes[].value.messages[]``, signed with
``X-Hub-Signature-256: sha256=<hex HMAC-SHA256(app_secret, raw body)>``. Status
updates (``value.statuses``), non-text messages and payloads for another phone
number are acknowledged and ignored. The subscription handshake (``GET``)
checks ``hub.verify_token`` and echoes ``hub.challenge``. Nothing configured
means every request is rejected (fail closed).

The ``:hook`` path parameter is the bot's **phone number id**, which is also
the sender on the way out. Names map onto the standard ones here: the
customer's ``wa_id`` → ``principal_id`` (and the tenant: sender-scoped), and
the 1:1 chat → ``session_id`` (the same ``wa_id``). A reply quotes the message
it answers via ``context.message_id``.
"""

import hashlib
import hmac
import json
from typing import Any

import httpx

from agent.domain.agent import InboundEvent
from agent.edges.channels import (
    BadRequest,
    InboundRequest,
    OutboundMessage,
    RoutedEvent,
    Unauthorized,
)

GRAPH_BASE = "https://graph.facebook.com"


class WhatsAppAdapter:
    def __init__(
        self,
        verify_token: str | None,
        app_secret: str | None,
        http: httpx.AsyncClient | None = None,
        access_token: str | None = None,
        api_base_url: str = GRAPH_BASE,
        graph_version: str = "v21.0",
    ) -> None:
        self._verify_token = verify_token
        self._app_secret = app_secret
        self._http = http
        self._access_token = access_token
        self._base = api_base_url.rstrip("/")
        self._version = graph_version

    # --- handshake ----------------------------------------------------------------

    def verify(self, request: InboundRequest) -> str:
        if self._verify_token is None:
            raise Unauthorized("verify token not configured")
        if request.query.get("hub.mode") != "subscribe":
            raise BadRequest("not a subscribe handshake")
        token = request.query.get("hub.verify_token", "")
        if not hmac.compare_digest(token, self._verify_token):
            raise Unauthorized("bad verify token")
        challenge = request.query.get("hub.challenge")
        if not challenge:
            raise BadRequest("missing hub.challenge")
        return challenge

    # --- in -----------------------------------------------------------------------

    def parse_inbound(self, request: InboundRequest) -> RoutedEvent | None:
        if self._app_secret is None:
            raise Unauthorized("app secret not configured")
        if not request.body:
            raise BadRequest("missing body")
        self._check_signature(request.body, request.headers.get("x-hub-signature-256"))

        payload = _json(request.body)
        hook = request.path["hook"]
        message = _first_text_message(payload, phone_number_id=hook)
        if message is None:  # statuses, another number, or nothing we handle yet
            return None

        sender = message.get("from")
        text = (message.get("text") or {}).get("body")
        if not isinstance(sender, str) or not sender:
            raise BadRequest("missing message.from")
        if not isinstance(text, str) or not text.strip():
            return None  # media-only messages aren't handled yet
        return RoutedEvent(
            agent_key=hook,
            event=InboundEvent(
                event_id=message["id"],  # "wamid...": the idempotency key
                tenant_id=sender,
                session_id=sender,  # a 1:1 chat: the session is the customer
                principal_id=sender,
                text=text,
            ),
        )

    def _check_signature(self, body: bytes, header: str | None) -> None:
        assert self._app_secret is not None
        digest = hmac.new(self._app_secret.encode(), body, hashlib.sha256).hexdigest()
        expected = f"sha256={digest}"
        if not header or not hmac.compare_digest(header.lower(), expected):
            raise Unauthorized("bad signature")

    # --- out ----------------------------------------------------------------------

    async def send(self, message: OutboundMessage) -> None:
        if self._http is None or not self._access_token:
            raise RuntimeError("WhatsApp outbound is not configured")
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": message.session_id,
            "type": "text",
            "text": {"body": message.text},
        }
        if message.reply_to_event_id:  # quote the message this answers
            payload["context"] = {"message_id": message.reply_to_event_id}
        response = await self._http.post(
            f"{self._base}/{self._version}/{message.agent_key}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        response.raise_for_status()


def _json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise BadRequest("malformed JSON") from e
    if not isinstance(payload, dict):
        raise BadRequest("expected a JSON object")
    return payload


def _first_text_message(payload: dict[str, Any], phone_number_id: str) -> dict[str, Any] | None:
    """The first text message addressed to this bot number, or ``None``.

    Meta batches under load, but delivers one message per call in practice;
    handling only the first is a known simplification of this slice.
    """
    entries = payload.get("entry")
    if not isinstance(entries, list):
        raise BadRequest("missing entry")
    for entry in entries:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            if metadata.get("phone_number_id") != phone_number_id:
                continue  # authentic, but for another bot number
            for message in value.get("messages") or []:  # absent on status updates
                if message.get("type") == "text" and isinstance(message.get("id"), str):
                    return message
    return None
