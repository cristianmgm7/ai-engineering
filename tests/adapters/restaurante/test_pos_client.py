"""Tests for adapters/restaurante/pos/client.py — the POS vendor client.

No network: httpx.MockTransport plays the POS. The negatives matter most:
errors must become PosError, never leak httpx exceptions upward.
"""

import httpx
import pytest
from pydantic import SecretStr

from agent.adapters.restaurante.pos.client import PosClient, PosError

BASE = "https://pos.test/api/v1"


def client_against(handler) -> tuple[PosClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return PosClient(http, BASE, SecretStr("pos-key-1")), seen


async def test_list_products_parses_the_catalog_and_sends_the_bearer():
    pos, seen = client_against(
        lambda r: httpx.Response(
            200,
            json={
                "products": [
                    {"sku": "TAC-01", "name": "Taco al pastor", "price_cents": 2500},
                    {"sku": "AGU-02", "name": "Agua de horchata", "price_cents": 1200},
                ]
            },
        )
    )
    products = await pos.list_products()
    assert [p.sku for p in products] == ["TAC-01", "AGU-02"]
    assert seen[0].url == httpx.URL(f"{BASE}/products")
    assert seen[0].headers["authorization"] == "Bearer pos-key-1"


async def test_create_order_posts_items_and_idempotency_key():
    pos, seen = client_against(
        lambda r: httpx.Response(
            201,
            json={
                "id": "ord-9",
                "items": ["2 tacos"],
                "status": "received",
                "created_at": "2026-10-09T12:00:00Z",
            },
        )
    )
    order = await pos.create_order(["2 tacos"], idempotency_key="key-abc")
    assert order.id == "ord-9"
    assert seen[0].method == "POST"
    assert seen[0].headers["idempotency-key"] == "key-abc"


async def test_an_http_error_becomes_a_readable_pos_error():
    pos, _ = client_against(lambda r: httpx.Response(503, text="POS mantenimiento"))
    with pytest.raises(PosError) as e:
        await pos.list_products()
    assert e.value.status_code == 503
    assert "mantenimiento" in e.value.detail


async def test_a_network_failure_becomes_pos_error_not_httpx():
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    pos, _ = client_against(explode)
    with pytest.raises(PosError) as e:
        await pos.get_order("ord-1")
    assert e.value.status_code == 0  # we never got an answer


async def test_a_payload_without_the_products_list_is_rejected():
    pos, _ = client_against(lambda r: httpx.Response(200, json={"whatever": 1}))
    with pytest.raises(PosError):
        await pos.list_products()
