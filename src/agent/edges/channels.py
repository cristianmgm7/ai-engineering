"""L4 · Channels — the kernel contract every channel implements.

A ``ChannelAdapter`` turns a raw webhook into a ``RoutedEvent`` (which agent, and
the standard ``InboundEvent``) and sends replies back. It is where a channel's
own names (WhatsApp ``wa_id``, chat id) become standard ones
(``session_id``, ``principal_id``). Nothing behind it knows which channel a
message came from.

``parse_inbound`` returns ``None`` for a request that is authentic but not for us
(the agent's own message echoing back, an event type we don't handle): the
ingress answers 200 so the channel doesn't retry, and nothing runs.
"""

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.domain.agent import InboundEvent


class InboundRequest(BaseModel):
    """What the ingress hands a channel: the raw bytes (signatures are computed over
    them), lower-cased headers, the path parameters and the query string."""

    model_config = ConfigDict(frozen=True)

    body: bytes
    headers: Mapping[str, str]
    path: Mapping[str, str]
    query: Mapping[str, str] = Field(default_factory=dict)


class RoutedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_key: str  # which agent this is for (e.g. the bot's platform user/number id)
    event: InboundEvent


class OutboundMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_key: str  # who speaks
    session_id: str  # where
    text: str
    reply_to_event_id: str | None = None  # also the idempotency key for the send


class RejectedRequest(Exception):
    """Raised by ``parse_inbound``; the ingress answers with ``status_code``."""

    status_code = 400

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BadRequest(RejectedRequest):
    status_code = 400


class Unauthorized(RejectedRequest):
    status_code = 401


class ChannelAdapter(Protocol):
    def parse_inbound(self, request: InboundRequest) -> RoutedEvent | None:
        """Verify and normalize. Raises ``RejectedRequest``; ``None`` = authentic but
        ignored."""
        ...

    def verify(self, request: InboundRequest) -> str | None:
        """Answer the channel's subscription handshake (``GET``, e.g. Meta's
        ``hub.challenge``): the exact text to echo back. Raises ``RejectedRequest``
        on a bad token; ``None`` = this channel has no handshake (the ingress 404s).
        """
        ...

    async def send(self, message: OutboundMessage) -> None: ...
