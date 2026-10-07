"""Tests for platform/cost.py."""

import pytest

from agent.platform.cost import ModelPrice, cost_of, cost_usd
from agent.platform.model import Usage


def test_cost_adds_every_token_kind_per_million():
    price = ModelPrice(input=2.0, output=10.0, cache_read=0.2, cache_write=2.5)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
        cache_write_tokens=200_000,
    )
    assert cost_usd(usage, price) == pytest.approx(2.0 + 1.0 + 0.1 + 0.5)


def test_unknown_model_has_no_cost():
    assert cost_of("not-a-model", Usage(input_tokens=10)) is None


def test_known_model_is_priced():
    assert cost_of("claude-sonnet-5", Usage(input_tokens=1_000_000)) == pytest.approx(2.0)
