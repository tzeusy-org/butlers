"""Smoke tests: dashboard health endpoints (GET /api/health and GET /health).

Cases:
- Both routes return 200 {\"status\": \"ok\"} after startup completes.
- Both routes succeed WITHOUT an X-API-Key header when auth is enabled
  (they are listed in ``_PUBLIC_PATHS`` and must never require a key).
- Both routes return 503 {\"status\": \"starting\"} before lifespan startup
  completes — health probes must not advertise readiness prematurely.

No LLM is spawned and no database connection is required.
``httpx.AsyncClient`` with ``ASGITransport`` does NOT trigger the ASGI
lifespan, so the ``before-startup`` tests obtain the pre-startup state
automatically.  Post-startup tests set ``app.state.ready = True`` directly
to simulate completed startup without a real DB.
"""

from __future__ import annotations

import httpx
import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEALTH_PATHS = ["/api/health", "/health"]


def _started_app(api_key: str = "") -> object:
    """Return a dashboard app with startup state simulated as complete.

    ``httpx.AsyncClient`` with ``ASGITransport`` does not trigger the ASGI
    lifespan, so we set ``app.state.ready = True`` directly to replicate the
    post-startup state that the lifespan would have established.
    """
    app = create_app(api_key=api_key)
    app.state.ready = True
    return app


# ---------------------------------------------------------------------------
# Healthy after startup — 200 {"status": "ok"}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _HEALTH_PATHS)
async def test_health_returns_ok_after_startup(path):
    """Both health routes return 200 {\"status\": \"ok\"} after startup completes."""
    app = _started_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200, (
        f"Expected 200 from {path} after startup, got {resp.status_code}"
    )
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Public paths — no API key required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _HEALTH_PATHS)
async def test_health_bypasses_api_key_auth(path):
    """Health routes return 200 without an X-API-Key even when auth is enabled.

    Both paths are registered in ``_PUBLIC_PATHS`` in ``butlers.api.middleware``
    and must never require an API key, even when ``DASHBOARD_API_KEY`` is set.
    """
    app = _started_app(api_key="super-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)  # intentionally no X-API-Key header
    assert resp.status_code == 200, (
        f"Expected 200 (public path bypasses auth), got {resp.status_code} on {path}"
    )


# ---------------------------------------------------------------------------
# Not healthy before lifespan startup completes — 503
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _HEALTH_PATHS)
async def test_health_not_ready_before_lifespan_startup(path):
    """Health routes return 503 before the lifespan startup phase completes.

    ``create_app`` initialises ``app.state.ready = False``.  Because
    ``httpx.AsyncClient`` with ``ASGITransport`` does not trigger the ASGI
    lifespan, ``ready`` remains ``False`` during the request — exactly the
    pre-startup condition a health probe would observe if the server received
    a request before its lifespan finished.
    """
    app = create_app(api_key="")
    # No startup has run; app.state.ready is False (set by create_app).
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 503, (
        f"Expected 503 (startup not complete) from {path}, got {resp.status_code}"
    )
