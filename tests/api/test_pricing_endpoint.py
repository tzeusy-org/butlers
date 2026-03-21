"""Tests for GET /api/settings/pricing endpoint.

Covers:
- Flat model returns correct per-million prices
- Zero-cost model returns { input_per_million: 0, output_per_million: 0 }
- Tiered model returns base tier (context_threshold=0) prices
- Response only contains models that have pricing entries
"""

from __future__ import annotations

import httpx
import pytest

from butlers.api.deps import get_pricing
from butlers.api.pricing import (
    ModelPricing,
    PricingConfig,
    PricingTier,
    TieredModelPricing,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _override_pricing(app, config: PricingConfig):
    """Override the pricing dependency on the shared app."""
    app.dependency_overrides[get_pricing] = lambda: config


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPricingEndpoint:
    async def test_flat_model_returns_per_million_prices(self, app):
        config = PricingConfig(
            {
                "claude-sonnet-4-5": ModelPricing(
                    input_price_per_token=0.000003,
                    output_price_per_token=0.000015,
                ),
            }
        )
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        body = response.json()
        data = body["data"]
        assert "claude-sonnet-4-5" in data
        entry = data["claude-sonnet-4-5"]
        assert entry["input_per_million"] == pytest.approx(3.0)
        assert entry["output_per_million"] == pytest.approx(15.0)

    async def test_zero_cost_model(self, app):
        config = PricingConfig(
            {
                "free-model": ModelPricing(
                    input_price_per_token=0.0,
                    output_price_per_token=0.0,
                ),
            }
        )
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        entry = response.json()["data"]["free-model"]
        assert entry["input_per_million"] == 0.0
        assert entry["output_per_million"] == 0.0

    async def test_tiered_model_returns_base_tier(self, app):
        config = PricingConfig(
            {
                "gpt-5.4": TieredModelPricing(
                    tiers=(
                        PricingTier(
                            context_threshold=0,
                            input_price_per_token=0.0000025,
                            output_price_per_token=0.000015,
                        ),
                        PricingTier(
                            context_threshold=272_000,
                            input_price_per_token=0.000005,
                            output_price_per_token=0.0000225,
                        ),
                    )
                ),
            }
        )
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        entry = response.json()["data"]["gpt-5.4"]
        # Base tier: 0.0000025 * 1M = 2.5, 0.000015 * 1M = 15.0
        assert entry["input_per_million"] == pytest.approx(2.5)
        assert entry["output_per_million"] == pytest.approx(15.0)

    async def test_response_only_contains_known_models(self, app):
        config = PricingConfig(
            {
                "model-a": ModelPricing(
                    input_price_per_token=0.000001,
                    output_price_per_token=0.000002,
                ),
                "model-b": ModelPricing(
                    input_price_per_token=0.000003,
                    output_price_per_token=0.000006,
                ),
            }
        )
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data.keys()) == {"model-a", "model-b"}

    async def test_empty_pricing_config(self, app):
        config = PricingConfig({})
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        assert response.json()["data"] == {}

    async def test_mixed_flat_and_tiered(self, app):
        config = PricingConfig(
            {
                "flat-model": ModelPricing(
                    input_price_per_token=0.000001,
                    output_price_per_token=0.000002,
                ),
                "tiered-model": TieredModelPricing(
                    tiers=(
                        PricingTier(
                            context_threshold=0,
                            input_price_per_token=0.000010,
                            output_price_per_token=0.000020,
                        ),
                        PricingTier(
                            context_threshold=100_000,
                            input_price_per_token=0.000020,
                            output_price_per_token=0.000040,
                        ),
                    )
                ),
            }
        )
        _override_pricing(app, config)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/pricing")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["flat-model"]["input_per_million"] == pytest.approx(1.0)
        assert data["flat-model"]["output_per_million"] == pytest.approx(2.0)
        assert data["tiered-model"]["input_per_million"] == pytest.approx(10.0)
        assert data["tiered-model"]["output_per_million"] == pytest.approx(20.0)
