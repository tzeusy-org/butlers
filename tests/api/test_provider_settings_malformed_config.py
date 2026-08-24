"""Malformed ``provider_config.config`` rows must not take down the listing.

``GET /api/settings/providers`` lists every configured provider, so a single row
whose JSONB ``config`` is not a JSON object used to raise pydantic
ValidationError inside ``_row_to_provider`` and 500 the whole endpoint.

Contract verified here (bu-d65r2):
- a malformed row never 500s the listing,
- it stays *visible* in the listing rather than silently vanishing, with an
  empty ``config`` and ``config_available: false``,
- healthy rows alongside it are unaffected and report ``config_available: true``,
- ``POST /{provider_type}/test-connectivity`` names the unreadable config
  instead of reporting it as a merely absent probe URL.

All config payloads here are synthetic and carry no credentials.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.provider_settings import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(provider_type: str, raw_config: Any) -> dict[str, Any]:
    """Build a dict mimicking an asyncpg Record from public.provider_config."""
    return {
        "provider_type": provider_type,
        "display_name": f"{provider_type} display",
        "config": raw_config,
        "enabled": True,
    }


def _app_with_rows(rows: list[dict[str, Any]]):
    """Wire a fresh app whose shared credential pool returns ``rows``."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=rows[0] if rows else None)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


async def _list_providers(rows: list[dict[str, Any]]) -> httpx.Response:
    app = _app_with_rows(rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/settings/providers")


# ---------------------------------------------------------------------------
# Listing: malformed rows are degraded, never fatal, never invisible
# ---------------------------------------------------------------------------


async def test_scalar_config_does_not_500_the_listing() -> None:
    """A row storing a JSON scalar degrades itself and leaves the listing intact."""
    resp = await _list_providers(
        [
            _row("anthropic", '{"base_url": "http://synthetic.invalid"}'),
            _row("broken", "42"),
        ]
    )

    assert resp.status_code == 200
    providers = resp.json()["data"]
    assert [p["provider_type"] for p in providers] == ["anthropic", "broken"]

    healthy, broken = providers
    assert healthy["config"] == {"base_url": "http://synthetic.invalid"}
    assert healthy["config_available"] is True
    assert broken["config"] == {}
    assert broken["config_available"] is False


async def test_array_config_is_degraded_not_fatal() -> None:
    """A JSON array in the config column degrades that row only."""
    resp = await _list_providers([_row("broken", '["synthetic", "entries"]')])

    assert resp.status_code == 200
    (broken,) = resp.json()["data"]
    assert broken["config"] == {}
    assert broken["config_available"] is False


async def test_undecodable_config_string_is_flagged_not_silently_empty() -> None:
    """A config string that is not JSON at all must not look like an empty config."""
    resp = await _list_providers([_row("broken", "{not json")])

    assert resp.status_code == 200
    (broken,) = resp.json()["data"]
    assert broken["config"] == {}
    assert broken["config_available"] is False


async def test_non_string_non_dict_config_is_flagged() -> None:
    """A decoded-but-non-object value (list) must not look like an empty config."""
    resp = await _list_providers([_row("broken", ["synthetic"])])

    assert resp.status_code == 200
    (broken,) = resp.json()["data"]
    assert broken["config"] == {}
    assert broken["config_available"] is False


async def test_dict_config_round_trips_unflagged() -> None:
    """An already-decoded dict config is passed through and reported healthy."""
    resp = await _list_providers([_row("ollama", {"base_url": "http://synthetic.invalid"})])

    assert resp.status_code == 200
    (provider,) = resp.json()["data"]
    assert provider["config"] == {"base_url": "http://synthetic.invalid"}
    assert provider["config_available"] is True


# ---------------------------------------------------------------------------
# test-connectivity: an unreadable config is named, not mistaken for an absent one
# ---------------------------------------------------------------------------


async def test_connectivity_names_unreadable_config() -> None:
    """A malformed config reports itself, not a misleading 'no probe URL'."""
    app = _app_with_rows([_row("ollama", "42")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/settings/providers/ollama/test-connectivity")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success"] is False
    assert data["error"] == "Stored provider config is not a readable JSON object"
