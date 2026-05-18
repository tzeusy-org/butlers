"""Tests for GET /api/ingestion/connectors/available endpoint.

Covers:
- Returns 200 with an array of connector profiles
- Each profile has required fields (connector_type, channel, provider, display_name,
  supports_backfill)
- Response does NOT depend on any database / connector_registry rows
- Known connector types are present in the response

§3.5 / §3.12 — Phase 3d (bu-1f91v.9)
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.unit


async def test_available_connectors_200(app):
    """GET /api/ingestion/connectors/available returns 200 with profile list."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/available")

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert isinstance(body["data"], list)
    assert len(body["data"]) > 0


async def test_available_connectors_schema(app):
    """Each profile has required fields per spec."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/available")

    assert resp.status_code == 200
    profiles = resp.json()["data"]

    required_fields = {"connector_type", "channel", "provider", "display_name", "supports_backfill"}
    for profile in profiles:
        for field in required_fields:
            assert field in profile, f"Profile missing required field: {field}"
        assert isinstance(profile["supports_backfill"], bool)


async def test_available_connectors_includes_gmail(app):
    """Gmail connector profile must be present in the catalog."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/available")

    profiles = {p["connector_type"]: p for p in resp.json()["data"]}
    assert "gmail" in profiles
    gmail = profiles["gmail"]
    assert gmail["channel"] == "email"
    assert gmail["provider"] == "google"
    assert gmail["supports_backfill"] is True


async def test_available_connectors_includes_telegram_bot(app):
    """Telegram bot connector profile must be present."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/available")

    profiles = {p["connector_type"]: p for p in resp.json()["data"]}
    assert "telegram_bot" in profiles
    assert profiles["telegram_bot"]["channel"] == "telegram"


async def test_available_connectors_no_db_dependency(app):
    """Endpoint requires no DB dependency — no dependency overrides needed."""
    # This test deliberately does NOT set up any DB mocks.
    # The endpoint must not fail due to missing DB.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/connectors/available")

    # Must succeed without any DB setup
    assert resp.status_code == 200
