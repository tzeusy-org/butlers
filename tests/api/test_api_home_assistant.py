"""Tests for Home Assistant settings API router.

Covers all endpoints with mocked entity_info functions and mocked HTTP validation:
- GET    /api/settings/home-assistant  — connection status
- POST   /api/settings/home-assistant  — validate and save credentials
- DELETE /api/settings/home-assistant  — remove credentials

HA HTTP validation calls are mocked via patch. The entity_info functions
(resolve/upsert/delete) are mocked to avoid needing a real database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers.home_assistant import (
    _get_db_manager,
    _HAValidationError,
    _mask_url,
    _validate_ha_connection,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_VALIDATE_PATCH = "butlers.api.routers.home_assistant._validate_ha_connection"
_RESOLVE_POOL_PATCH = "butlers.api.routers.home_assistant._resolve_pool"
_RESOLVE_EI_PATCH = "butlers.api.routers.home_assistant.resolve_owner_entity_info"
_UPSERT_EI_PATCH = "butlers.api.routers.home_assistant.upsert_owner_entity_info"
_DELETE_EI_PATCH = "butlers.api.routers.home_assistant.delete_owner_entity_info"

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_db_manager() -> MagicMock:
    """Build a mock DatabaseManager with credential_shared_pool."""
    pool = MagicMock()
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    return db_manager


def _make_resolve_side_effect(
    *,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> AsyncMock:
    """Return an AsyncMock for resolve_owner_entity_info with per-type values."""
    values = {
        "home_assistant_url": ha_url,
        "home_assistant_token": ha_token,
    }

    async def _resolve(_pool, info_type: str) -> str | None:
        return values.get(info_type)

    return AsyncMock(side_effect=_resolve)


# ---------------------------------------------------------------------------
# App fixture builder
# ---------------------------------------------------------------------------


def _build_app(has_db: bool = True):
    """Return an app with mocked DB dependency wired in."""
    from butlers.api.app import create_app

    _app = create_app(api_key="")

    if has_db:
        db_manager = _make_db_manager()
        _app.dependency_overrides[_get_db_manager] = lambda: db_manager

    return _app


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestMaskUrl:
    def test_strips_path(self):
        assert _mask_url("http://homeassistant.local:8123/api/states") == (
            "http://homeassistant.local:8123"
        )

    def test_strips_query(self):
        assert _mask_url("http://homeassistant.local:8123/api/?x=1") == (
            "http://homeassistant.local:8123"
        )

    def test_bare_origin(self):
        assert _mask_url("http://homeassistant.local:8123") == ("http://homeassistant.local:8123")

    def test_https(self):
        assert _mask_url("https://ha.example.com/api/") == "https://ha.example.com"

    def test_localhost(self):
        assert _mask_url("http://localhost:8123/api/") == "http://localhost:8123"


# ---------------------------------------------------------------------------
# Unit tests for _validate_ha_connection
# ---------------------------------------------------------------------------


class TestValidateHAConnection:
    async def test_success_on_200(self):
        """Validation passes when HA returns HTTP 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should not raise
            await _validate_ha_connection("http://ha.local:8123", "mytoken")

        mock_client.get.assert_called_once_with(
            "http://ha.local:8123/api/",
            headers={"Authorization": "Bearer mytoken"},
        )

    async def test_raises_unreachable_on_request_error(self):
        """Validation raises unreachable on httpx.RequestError."""
        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_HAValidationError) as exc_info:
                await _validate_ha_connection("http://ha.local:8123", "mytoken")

        assert exc_info.value.category == "unreachable"

    async def test_raises_unreachable_on_timeout(self):
        """Validation raises unreachable on httpx.TimeoutException."""
        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_HAValidationError) as exc_info:
                await _validate_ha_connection("http://ha.local:8123", "mytoken")

        assert exc_info.value.category == "unreachable"

    async def test_raises_auth_failure_on_401(self):
        """Validation raises auth_failure when HA returns HTTP 401."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_HAValidationError) as exc_info:
                await _validate_ha_connection("http://ha.local:8123", "bad_token")

        assert exc_info.value.category == "auth_failure"

    async def test_raises_auth_failure_on_403(self):
        """Validation raises auth_failure when HA returns HTTP 403."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_HAValidationError) as exc_info:
                await _validate_ha_connection("http://ha.local:8123", "bad_token")

        assert exc_info.value.category == "auth_failure"

    async def test_raises_unexpected_on_500(self):
        """Validation raises unexpected when HA returns an unexpected status code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(_HAValidationError) as exc_info:
                await _validate_ha_connection("http://ha.local:8123", "mytoken")

        assert exc_info.value.category == "unexpected"

    async def test_url_trailing_slash_stripped(self):
        """Trailing slash in URL is stripped before appending /api/."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("butlers.api.routers.home_assistant.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await _validate_ha_connection("http://ha.local:8123/", "mytoken")

        # Should call /api/ only once (not //api/)
        call_url = mock_client.get.call_args[0][0]
        assert call_url == "http://ha.local:8123/api/"


# ---------------------------------------------------------------------------
# GET /api/settings/home-assistant
# ---------------------------------------------------------------------------


class TestGetHAStatus:
    async def test_not_configured_when_no_credentials(self):
        """Returns not_configured when no credentials are stored."""
        app = _build_app()
        mock_resolve = _make_resolve_side_effect()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=mock_resolve.side_effect),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "not_configured"
        assert body["url_configured"] is False
        assert body["token_configured"] is False
        assert body["masked_url"] is None

    async def test_not_configured_when_only_url(self):
        """Returns not_configured when URL is set but token is missing."""
        app = _build_app()
        mock_resolve = _make_resolve_side_effect(ha_url="http://ha.local:8123")

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=mock_resolve.side_effect),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "not_configured"
        assert body["url_configured"] is True
        assert body["token_configured"] is False

    async def test_not_configured_when_only_token(self):
        """Returns not_configured when token is set but URL is missing."""
        app = _build_app()
        mock_resolve = _make_resolve_side_effect(ha_token="mytoken")

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=mock_resolve.side_effect),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "not_configured"
        assert body["url_configured"] is False
        assert body["token_configured"] is True

    async def test_connected_when_both_credentials_present(self):
        """Returns connected when both URL and token are stored."""
        app = _build_app()
        mock_resolve = _make_resolve_side_effect(
            ha_url="http://homeassistant.local:8123",
            ha_token="secret_token",
        )

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=mock_resolve.side_effect),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "connected"
        assert body["url_configured"] is True
        assert body["token_configured"] is True
        assert body["masked_url"] == "http://homeassistant.local:8123"

    async def test_masked_url_strips_path(self):
        """Masked URL contains only origin even if stored URL has a path."""
        app = _build_app()
        mock_resolve = _make_resolve_side_effect(
            ha_url="http://homeassistant.local:8123/api/",
            ha_token="secret_token",
        )

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=mock_resolve.side_effect),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        body = resp.json()
        assert body["masked_url"] == "http://homeassistant.local:8123"

    async def test_not_configured_when_no_db(self):
        """Returns not_configured gracefully when pool is unavailable."""
        app = _build_app(has_db=False)

        with patch(_RESOLVE_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "not_configured"


# ---------------------------------------------------------------------------
# POST /api/settings/home-assistant
# ---------------------------------------------------------------------------


class TestConfigureHA:
    async def test_success_stores_credentials(self):
        """Successful validation stores both URL and token and returns success."""
        app = _build_app()
        mock_upsert = AsyncMock(return_value=True)

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_VALIDATE_PATCH, return_value=None) as mock_validate,
            patch(_UPSERT_EI_PATCH, side_effect=mock_upsert.side_effect) as upsert_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={
                        "url": "http://homeassistant.local:8123",
                        "token": "my_long_lived_token",
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["masked_url"] == "http://homeassistant.local:8123"
        assert "validated" in body["message"].lower()

        # Validation was called with provided URL and token
        mock_validate.assert_called_once_with(
            "http://homeassistant.local:8123", "my_long_lived_token"
        )

        # Both entity_info types upserted
        assert upsert_mock.call_count == 2
        upserted_types = {call.args[1] for call in upsert_mock.call_args_list}
        assert upserted_types == {"home_assistant_url", "home_assistant_token"}

    async def test_token_stored_as_secured(self):
        """Token is stored with secured=True."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_VALIDATE_PATCH, return_value=None),
            patch(_UPSERT_EI_PATCH, new_callable=AsyncMock, return_value=True) as upsert_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "token123"},
                )

        # Find the call that stored the token
        token_call = next(
            c for c in upsert_mock.call_args_list if c.args[1] == "home_assistant_token"
        )
        assert token_call.kwargs.get("secured") is True

    async def test_url_stored_as_not_secured(self):
        """URL is stored with secured=False."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_VALIDATE_PATCH, return_value=None),
            patch(_UPSERT_EI_PATCH, new_callable=AsyncMock, return_value=True) as upsert_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "token123"},
                )

        url_call = next(
            c for c in upsert_mock.call_args_list if c.args[1] == "home_assistant_url"
        )
        assert url_call.kwargs.get("secured") is False

    async def test_returns_502_on_unreachable(self):
        """Returns 502 when HA cannot be reached."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(
                _VALIDATE_PATCH,
                side_effect=_HAValidationError("Cannot connect", category="unreachable"),
            ),
            patch(_UPSERT_EI_PATCH, new_callable=AsyncMock, return_value=True) as upsert_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "mytoken"},
                )

        assert resp.status_code == 502
        assert "Cannot connect" in resp.json()["detail"]

        # Credentials must NOT be stored on validation failure
        upsert_mock.assert_not_called()

    async def test_returns_502_on_auth_failure(self):
        """Returns 502 with informative message when authentication fails."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(
                _VALIDATE_PATCH,
                side_effect=_HAValidationError(
                    "Authentication failed (HTTP 401). Check the token.",
                    category="auth_failure",
                ),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "bad_token"},
                )

        assert resp.status_code == 502
        assert "Authentication failed" in resp.json()["detail"]

    async def test_returns_502_on_unexpected_status(self):
        """Returns 502 when HA returns an unexpected HTTP status code."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(
                _VALIDATE_PATCH,
                side_effect=_HAValidationError(
                    "Unexpected response (HTTP 500).", category="unexpected"
                ),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "mytoken"},
                )

        assert resp.status_code == 502

    async def test_returns_503_when_no_db(self):
        """Returns 503 when database pool is unavailable."""
        app = _build_app(has_db=False)

        with patch(_RESOLVE_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "mytoken"},
                )

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_validation_error_empty_url(self):
        """Returns 422 when URL is empty string."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/settings/home-assistant",
                json={"url": "", "token": "mytoken"},
            )

        assert resp.status_code == 422

    async def test_validation_error_empty_token(self):
        """Returns 422 when token is empty string."""
        app = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/settings/home-assistant",
                json={"url": "http://ha.local:8123", "token": ""},
            )

        assert resp.status_code == 422

    async def test_masked_url_in_response_does_not_include_token(self):
        """Response masked_url must not contain the token."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_VALIDATE_PATCH, return_value=None),
            patch(_UPSERT_EI_PATCH, new_callable=AsyncMock, return_value=True),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={"url": "http://ha.local:8123", "token": "super_secret_token"},
                )

        body = resp.json()
        assert "super_secret_token" not in str(body)


# ---------------------------------------------------------------------------
# DELETE /api/settings/home-assistant
# ---------------------------------------------------------------------------


class TestDeleteHAConfig:
    async def test_deletes_both_credentials(self):
        """DELETE removes both entity_info types and returns success."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(
                _DELETE_EI_PATCH, new_callable=AsyncMock, return_value=True
            ) as delete_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        # Both entity_info types were deleted
        deleted_types = {call.args[1] for call in delete_mock.call_args_list}
        assert deleted_types == {"home_assistant_url", "home_assistant_token"}

    async def test_idempotent_when_no_credentials(self):
        """DELETE succeeds even when no credentials are stored (idempotent)."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_DELETE_EI_PATCH, new_callable=AsyncMock, return_value=False),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    async def test_success_when_no_db(self):
        """DELETE returns success even when pool is unavailable."""
        app = _build_app(has_db=False)

        with patch(_RESOLVE_POOL_PATCH, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "unavailable" in body["message"].lower()

    async def test_response_message_includes_count(self):
        """DELETE response message includes the count of deleted credentials."""
        app = _build_app()

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_DELETE_EI_PATCH, new_callable=AsyncMock, return_value=True),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        body = resp.json()
        # 2 credentials deleted
        assert "2" in body["message"]
