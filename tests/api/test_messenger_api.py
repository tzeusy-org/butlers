"""Constructed-app absence coverage for retired Messenger tracking endpoints."""

from __future__ import annotations

import httpx
import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.unit

_RETIRED_MESSENGER_PATHS = (
    "/api/messenger/delivery-stats",
    "/api/messenger/circuit-status",
    "/api/messenger/queue-depth",
    "/api/messenger/dead-letters",
)


async def test_constructed_dashboard_app_does_not_register_retired_messenger_paths() -> None:
    """A fresh app must answer 404, not fabricated empty tracking health."""
    app = create_app(api_key="")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in _RETIRED_MESSENGER_PATHS:
            response = await client.get(path)
            assert response.status_code == 404, path
