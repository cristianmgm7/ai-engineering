"""L4 · Ingress — the HTTP front door (tb-agent doc 10).

``POST /webhooks/{channel}/{hook}``: hand the raw request to that channel's
adapter, check the agent exists, enqueue, answer fast. It never runs the agent.

- 401 / 400: the adapter rejected the request (bad signature, malformed body).
- 404: unknown channel or agent.
- 200 with ``ignored``: authentic but not for us; the channel shouldn't retry.
- 503: the queue is unavailable, so the channel retries delivery instead of the
  message being dropped.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.edges.channels import ChannelAdapter, InboundRequest, RejectedRequest, RoutedEvent

logger = logging.getLogger(__name__)


class EventQueue(Protocol):
    async def enqueue(self, routed: RoutedEvent) -> None: ...


class AgentLookup(Protocol):
    def __contains__(self, agent_key: object) -> bool: ...


@dataclass(frozen=True)
class ChannelRoute:
    adapter: ChannelAdapter
    queue: EventQueue


def create_ingress(routes: dict[str, ChannelRoute], agents: AgentLookup) -> FastAPI:
    app = FastAPI(title="agent ingress", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/webhooks/{channel}/{hook}")
    async def receive(channel: str, hook: str, request: Request) -> JSONResponse:
        route = routes.get(channel)
        if route is None:
            return JSONResponse({"error": "unknown channel"}, status_code=404)

        inbound = InboundRequest(
            body=await request.body(),
            headers={k.lower(): v for k, v in request.headers.items()},
            path={"hook": hook},
        )
        try:
            routed = route.adapter.parse_inbound(inbound)
        except RejectedRequest as e:
            return JSONResponse({"error": e.reason}, status_code=e.status_code)

        if routed is None:
            return JSONResponse({"ok": True, "ignored": True})
        if routed.agent_key not in agents:
            return JSONResponse({"error": "unknown agent"}, status_code=404)

        try:
            await route.queue.enqueue(routed)
        except Exception:
            logger.exception("enqueue failed for %s event %s", channel, routed.event.event_id)
            return JSONResponse({"error": "try again"}, status_code=503)
        return JSONResponse({"ok": True})

    return app
