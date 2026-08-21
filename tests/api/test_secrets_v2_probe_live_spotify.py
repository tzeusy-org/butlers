"""Spotify probe is connector-owned and unavailable through generic Secrets."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from tests.api.test_secrets_v2_mutations import (
    _build_app,
    _make_db,
    _make_entity_info_row,
)

pytestmark = pytest.mark.unit


def test_generic_spotify_probe_returns_404_without_provider_call(monkeypatch) -> None:
    row = _make_entity_info_row(
        info_type="spotify_oauth_refresh",
        value="fixture-refresh-value",
    )
    client = _build_app(_make_db(user_row=row, oauth_app_configured=True))
    provider_call = AsyncMock(side_effect=AssertionError("generic probe called Spotify"))
    monkeypatch.setattr(httpx.AsyncClient, "post", provider_call)
    monkeypatch.setattr(httpx.AsyncClient, "get", provider_call)

    response = client.post("/api/secrets/user/spotify/probe")

    assert response.status_code == 404
    provider_call.assert_not_awaited()


def test_generic_spotify_probe_response_contains_no_credential_material() -> None:
    row = _make_entity_info_row(
        info_type="spotify_oauth_access",
        value="fixture-access-value",
    )
    client = _build_app(_make_db(user_row=row, oauth_app_configured=True))

    response = client.post("/api/secrets/user/spotify/probe")

    assert response.status_code == 404
    assert response.json() == {"detail": "Credential not found"}
