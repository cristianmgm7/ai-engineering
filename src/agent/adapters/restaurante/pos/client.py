"""The POS vendor client: speaks VectorPOS's HTTP API and nothing else.

VectorPOS (docs: vectorpos.readme.io) — the pieces we use:

- ``GET  /products``      → the branch's catalog (a bare JSON array)
- ``POST /invoice``       → register an order ("Registrar pedido")
- ``GET  /invoice/{id}``  → one registered order ("Consultar cuenta")

Auth is HTTP Basic: username = the authorized VectorPOS user, password = the
API token generated for the *branch* (one token per sucursal, created in the
admin panel under "Conecta VectorPOS").

Two contract quirks that shape everything above this module:

- **No idempotency**: ``POST /invoice`` has no idempotency key, so a blind
  retry duplicates the order. This client never retries; the caller keeps the
  pedido → invoice id mapping itself (``stores.py``).
- **Errors come in two shapes**: HTTP errors (401 ``{"msg": "Token inválido"}``,
  400, 404) and *business* refusals inside a 200 (``status: false`` with
  ``error.msg``, e.g. not enough inventory). Both become ``PosError`` here, like
  ``SendFailed`` does for the channel, so nothing above handles httpx or shapes.

The DTOs mirror the POS's JSON exactly, camelCase included — ours is
``domain/restaurante``; the translation happens in ``stores.py``, never here.
"""

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError


class PosProduct(BaseModel):
    """DTO: a catalog ``Product``. ``id`` doubles as the code ``POST /invoice``
    expects in ``items[].code``; ``price`` is in the branch's currency (COP),
    a plain number. Option groups (combos, pizzas, additionals) exist in the
    payload but are not modeled yet — plain items first."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    price: float
    category: str | None = None


class PosClientData(BaseModel):
    """DTO: the ``client`` object — all three fields are required by the POS."""

    model_config = ConfigDict(frozen=True)

    name: str
    phone: str
    address: str


class PosNewItem(BaseModel):
    """DTO: one ``items[]`` entry of ``POST /invoice``. ``priceUnit`` must come
    from the catalog — the POS trusts it, so nobody upstream may invent it."""

    model_config = ConfigDict(frozen=True)

    code: str
    amount: float
    priceUnit: float
    details: str | None = None


class PosNewInvoice(BaseModel):
    """DTO: the ``POST /invoice`` body ("Registrar pedido")."""

    model_config = ConfigDict(frozen=True)

    client: PosClientData
    items: tuple[PosNewItem, ...]
    delivery_price: float = 0
    delivery_details: str = ""


class PosInvoiceItem(BaseModel):
    """DTO: one ``items[]`` entry as ``GET /invoice/{id}`` returns it."""

    model_config = ConfigDict(frozen=True)

    name: str
    code: str
    amount: float
    details: str | None = None


class PosInvoice(BaseModel):
    """DTO: a registered order. The POS exposes no dates and no kitchen or
    delivery state — only ``totalPaid`` hints at progress."""

    model_config = ConfigDict(frozen=True)

    id: str
    items: tuple[PosInvoiceItem, ...]
    totalPaid: float = 0


class PosError(Exception):
    """The POS refused or the call failed. Carries the POS's own explanation;
    status_code 0 means we never got a response (network error)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"POS error ({status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail


class PosClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, user: str, token: SecretStr) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(user, token.get_secret_value())

    async def list_products(self) -> list[PosProduct]:
        data = await self._request("GET", "/products")
        if not isinstance(data, list):
            raise PosError(200, "unexpected POS payload: /products should be a list")
        return [PosProduct.model_validate(p) for p in data]

    async def create_invoice(self, draft: PosNewInvoice) -> str:
        """Register an order; returns the POS's invoice id. A 200 can still be a
        refusal (``status: false``) — that surfaces as ``PosError`` too. Never
        retry a timed-out call: there is no idempotency key to make it safe."""
        data = await self._request("POST", "/invoice", json=draft.model_dump(exclude_none=True))
        if not isinstance(data, dict):
            raise PosError(200, "unexpected POS payload: /invoice should be an object")
        if not data.get("status") or not data.get("resp"):
            error = data.get("error") or {}
            msg = error.get("msg") if isinstance(error, dict) else str(error)
            raise PosError(200, msg or "el POS no registró el pedido")
        return str(data["resp"])

    async def get_invoice(self, invoice_id: str) -> PosInvoice:
        data = await self._request("GET", f"/invoice/{invoice_id}")
        try:
            return PosInvoice.model_validate(data)
        except ValidationError as e:
            # A 200 without the invoice fields is the POS's "not found"
            # (``{"msg": "..."}``); anything else malformed lands here too.
            msg = data.get("msg") if isinstance(data, dict) else None
            raise PosError(404 if msg else 200, msg or f"invalid invoice payload: {e}") from e

    async def _request(self, method: str, path: str, json: dict | None = None) -> object:
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", json=json, auth=self._auth
            )
        except httpx.HTTPError as e:  # network: we never got an answer
            raise PosError(0, str(e)) from e
        if response.status_code >= 400:
            raise PosError(response.status_code, response.text[:500])
        try:
            return response.json()
        except ValueError as e:
            raise PosError(response.status_code, f"invalid JSON from POS: {e}") from e


__all__ = [
    "PosClient",
    "PosClientData",
    "PosError",
    "PosInvoice",
    "PosInvoiceItem",
    "PosNewInvoice",
    "PosNewItem",
    "PosProduct",
]
