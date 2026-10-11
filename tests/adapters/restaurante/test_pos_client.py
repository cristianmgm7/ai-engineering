"""Tests for adapters/restaurante/pos/client.py — the VectorPOS client.

No network: httpx.MockTransport plays the POS. The negatives matter most:
errors must become PosError, never leak httpx exceptions upward — including
VectorPOS's business refusals hidden inside a 200.
"""

import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from agent.adapters.restaurante.pos.client import (
    PosClient,
    PosClientData,
    PosError,
    PosNewInvoice,
    PosNewItem,
)

BASE = "https://api.vectorpos.test"
USER = "cristian"
TOKEN = "token-sucursal-1"


def client_against(handler) -> tuple[PosClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return PosClient(http, BASE, USER, SecretStr(TOKEN)), seen


def draft(**overrides) -> PosNewInvoice:
    base = dict(
        client=PosClientData(name="Ana", phone="573001234567", address="Calle 1 # 2-3"),
        items=(PosNewItem(code="42110093", amount=1, priceUnit=12500),),
    )
    return PosNewInvoice(**{**base, **overrides})


async def test_list_products_parses_the_catalog_and_sends_basic_auth():
    pos, seen = client_against(
        lambda r: httpx.Response(
            200,
            json=[  # VectorPOS returns a bare array, not a wrapper object
                {"id": "42110093", "name": "COMBO HAMBURGUESA", "category": "H", "price": 12500},
                {"id": "11910174", "name": "GASEOSA", "category": "B", "price": 3000},
            ],
        )
    )
    products = await pos.list_products()
    assert [p.id for p in products] == ["42110093", "11910174"]
    assert seen[0].url == httpx.URL(f"{BASE}/products")
    credentials = base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()
    assert seen[0].headers["authorization"] == f"Basic {credentials}"


async def test_create_invoice_posts_the_vectorpos_shape_and_returns_the_id():
    pos, seen = client_against(lambda r: httpx.Response(200, json={"status": True, "resp": "2033"}))
    con_detalles = PosNewItem(code="42110093", amount=2, priceUnit=12500, details="Sin cebolla")
    invoice_id = await pos.create_invoice(draft(items=(con_detalles,)))
    assert invoice_id == "2033"
    assert seen[0].method == "POST" and seen[0].url == httpx.URL(f"{BASE}/invoice")
    body = json.loads(seen[0].content)
    assert body["client"] == {"name": "Ana", "phone": "573001234567", "address": "Calle 1 # 2-3"}
    assert body["items"] == [
        {"code": "42110093", "amount": 2, "priceUnit": 12500, "details": "Sin cebolla"}
    ]
    assert body["delivery_price"] == 0 and "delivery_details" in body


async def test_a_business_refusal_inside_a_200_becomes_pos_error():
    refusal = {"status": False, "error": {"msg": "El saldo de inventario no alcanza"}}
    pos, _ = client_against(lambda r: httpx.Response(200, json=refusal))
    with pytest.raises(PosError) as e:
        await pos.create_invoice(draft())
    assert "inventario" in e.value.detail


async def test_get_invoice_parses_the_registered_order():
    pos, _ = client_against(
        lambda r: httpx.Response(
            200,
            json={
                "id": "2033",
                "items": [{"name": "COMBO HAMBURGUESA", "code": "42110093", "amount": 1}],
                "totalPaid": 12500,
            },
        )
    )
    invoice = await pos.get_invoice("2033")
    assert invoice.id == "2033" and invoice.totalPaid == 12500
    assert invoice.items[0].name == "COMBO HAMBURGUESA"


async def test_a_200_with_only_a_msg_is_the_pos_not_found():
    pos, _ = client_against(lambda r: httpx.Response(200, json={"msg": "Cuenta no encontrada"}))
    with pytest.raises(PosError) as e:
        await pos.get_invoice("9999")
    assert e.value.status_code == 404 and "no encontrada" in e.value.detail


async def test_an_http_error_becomes_a_readable_pos_error():
    pos, _ = client_against(lambda r: httpx.Response(401, json={"msg": "Token inválido"}))
    with pytest.raises(PosError) as e:
        await pos.list_products()
    assert e.value.status_code == 401
    assert "Token inválido" in e.value.detail


async def test_a_network_failure_becomes_pos_error_not_httpx():
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    pos, _ = client_against(explode)
    with pytest.raises(PosError) as e:
        await pos.get_invoice("2033")
    assert e.value.status_code == 0  # we never got an answer


async def test_a_catalog_that_is_not_a_list_is_rejected():
    pos, _ = client_against(lambda r: httpx.Response(200, json={"whatever": 1}))
    with pytest.raises(PosError):
        await pos.list_products()
