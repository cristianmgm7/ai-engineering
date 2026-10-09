"""The POS vendor client: speaks the POS's HTTP API and nothing else.

Three endpoints (a hypothetical contract until the real POS exists; only this
module changes when it does):

- ``GET  /products``           → the store's catalog
- ``POST /orders``             → register an order
- ``GET  /orders/{order_id}``  → one order's current state

The DTOs mirror the POS's JSON exactly — ours is ``domain/restaurante``; the
translation happens in ``stores.py``, never here. Every failure (HTTP status or
network) becomes ``PosError``, like ``SendFailed`` does for the channel, so
nothing above this module handles httpx exceptions.
"""

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError


class PosProduct(BaseModel):
    """DTO: the POS's shape for a product, not our ``MenuItem``."""

    model_config = ConfigDict(frozen=True)

    sku: str
    name: str
    price_cents: int
    available: bool = True


class PosOrder(BaseModel):
    """DTO: the POS's shape for an order, not our ``Pedido``."""

    model_config = ConfigDict(frozen=True)

    id: str
    items: tuple[str, ...]
    status: str
    created_at: datetime


class PosError(Exception):
    """The POS refused or the call failed. Carries the POS's own explanation;
    status_code 0 means we never got a response (network error)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"POS error ({status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class PosClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: SecretStr) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def list_products(self) -> list[PosProduct]:
        data = await self._request("GET", "/products")
        return [PosProduct.model_validate(p) for p in self._expect_list(data, "products")]

    async def create_order(self, items: list[str], idempotency_key: str) -> PosOrder:
        """Register an order. ``idempotency_key`` lets the POS drop a duplicate
        POST if we retry; the caller must reuse the same key for the same intent."""
        data = await self._request(
            "POST",
            "/orders",
            json={"items": items},
            headers={"Idempotency-Key": idempotency_key},
        )
        return PosOrder.model_validate(data)

    async def get_order(self, order_id: str) -> PosOrder:
        data = await self._request("GET", f"/orders/{order_id}")
        return PosOrder.model_validate(data)

    async def _request(
        self, method: str, path: str, json: dict | None = None, headers: dict | None = None
    ) -> object:
        auth = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", json=json, headers={**auth, **(headers or {})}
            )
        except httpx.HTTPError as e:  # network: we never got an answer
            raise PosError(0, str(e)) from e
        if response.status_code >= 400:
            raise PosError(response.status_code, response.text[:500])
        try:
            return response.json()
        except ValueError as e:
            raise PosError(response.status_code, f"invalid JSON from POS: {e}") from e

    @staticmethod
    def _expect_list(data: object, key: str) -> list:
        """The catalog comes wrapped: ``{"products": [...]}``."""
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise PosError(200, f"unexpected POS payload: missing '{key}' list")
        return data[key]


# A malformed but 2xx body (missing fields) surfaces as pydantic's ValidationError;
# re-exported so callers can treat "POS spoke garbage" uniformly if they want to.
__all__ = ["PosClient", "PosError", "PosOrder", "PosProduct", "ValidationError"]
