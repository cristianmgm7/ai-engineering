"""L0 · Cost — token usage × price table → US dollars.

Prices are USD per million tokens, Anthropic first-party rates as of 2026-09-25.
Cache writes are priced at the 5-minute TTL (1.25× input). Re-check them against
the pricing page before trusting a report's dollars; the token counts are exact.
"""

from pydantic import BaseModel, ConfigDict

from agent.platform.model import Usage


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: float
    output: float
    cache_read: float
    cache_write: float


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0),
    "claude-sonnet-5-5": ModelPrice(input=2.0, output=10.0, cache_read=0.20, cache_write=2.5),
    "claude-sonnet-5": ModelPrice(input=2.0, output=10.0, cache_read=0.20, cache_write=2.5),
    "claude-haiku-4-5": ModelPrice(input=1.0, output=5.0, cache_read=0.10, cache_write=1.25),
}


def cost_usd(usage: Usage, price: ModelPrice) -> float:
    per_token = 1 / 1_000_000
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    ) * per_token


def cost_of(
    model: str, usage: Usage, prices: dict[str, ModelPrice] = DEFAULT_PRICES
) -> float | None:
    """The cost of ``usage`` on ``model``, or None when the model has no known price."""
    price = prices.get(model)
    return None if price is None else cost_usd(usage, price)
