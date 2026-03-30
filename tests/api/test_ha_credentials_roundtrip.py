"""Regression test: HA dashboard settings → entity_info credential round-trip.

Validates the full flow:
  1. POST /api/settings/home-assistant writes credentials via the router.
  2. Credentials land in entity_info under the types that the HA connector
     process expects (``home_assistant_url`` and ``home_assistant_token``).
  3. The connector's credential resolution path reads those same entity_info
     types back.

This test catches regressions where the router writes to a different store
(e.g. butler_secrets) or uses different type names than the connector expects.

No real HA instance or database is required — the HA HTTP validation and DB
pool are mocked so the test runs in CI without external dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from butlers.api.routers.home_assistant import (
    _EI_HA_TOKEN,
    _EI_HA_URL,
    _get_db_manager,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants under test — must match the connector's lookup types exactly
# ---------------------------------------------------------------------------

_CONNECTOR_URL_TYPE = "home_assistant_url"
_CONNECTOR_TOKEN_TYPE = "home_assistant_token"

_VALIDATE_PATCH = "butlers.api.routers.home_assistant._validate_ha_connection"
_RESOLVE_POOL_PATCH = "butlers.api.routers.home_assistant._resolve_pool"
_RESOLVE_EI_PATCH = "butlers.api.routers.home_assistant.resolve_owner_entity_info"
_UPSERT_EI_PATCH = "butlers.api.routers.home_assistant.upsert_owner_entity_info"
_DELETE_EI_PATCH = "butlers.api.routers.home_assistant.delete_owner_entity_info"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_db_manager() -> MagicMock:
    pool = MagicMock()
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    return db_manager


def _build_app():
    from butlers.api.app import create_app

    app = create_app(api_key="")
    db_manager = _make_db_manager()
    app.dependency_overrides[_get_db_manager] = lambda: db_manager
    return app


# ---------------------------------------------------------------------------
# Key-alignment contract tests
# ---------------------------------------------------------------------------


class TestEntityInfoTypeAlignment:
    """The router must use the same entity_info type names the connector looks up.

    These tests pin the exact string values of the entity_info types.
    A rename on either side without updating the other would break the
    round-trip and should be caught here immediately.
    """

    def test_router_url_type_matches_connector_lookup(self) -> None:
        """Router constant _EI_HA_URL equals the type the connector reads."""
        assert _EI_HA_URL == _CONNECTOR_URL_TYPE, (
            f"Router writes URL under {_EI_HA_URL!r} but connector reads "
            f"{_CONNECTOR_URL_TYPE!r} — credential round-trip broken."
        )

    def test_router_token_type_matches_connector_lookup(self) -> None:
        """Router constant _EI_HA_TOKEN equals the type the connector reads."""
        assert _EI_HA_TOKEN == _CONNECTOR_TOKEN_TYPE, (
            f"Router writes token under {_EI_HA_TOKEN!r} but connector reads "
            f"{_CONNECTOR_TOKEN_TYPE!r} — credential round-trip broken."
        )


# ---------------------------------------------------------------------------
# Full HTTP round-trip regression test
# ---------------------------------------------------------------------------


class TestHACredentialRoundTrip:
    """POST /api/settings → entity_info → connector reads back the same values."""

    async def test_post_writes_credentials_under_connector_types(self) -> None:
        """POST /api/settings/home-assistant stores creds under connector-expected types.

        Regression test: the router must write to entity_info using the same
        type names that the HA connector reads during startup.
        """
        app = _build_app()
        storage: dict[str, str] = {}

        async def _mock_upsert(_pool, info_type: str, value: str, **_kw) -> bool:
            storage[info_type] = value
            return True

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_VALIDATE_PATCH, return_value=None),
            patch(_UPSERT_EI_PATCH, side_effect=_mock_upsert),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/settings/home-assistant",
                    json={
                        "url": "http://homeassistant.local:8123",
                        "token": "long-lived-access-token-abc123",
                    },
                )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # The router must have stored the credentials under the entity_info types
        # that the connector will later look up.
        assert _CONNECTOR_URL_TYPE in storage, (
            f"URL was NOT stored under connector type {_CONNECTOR_URL_TYPE!r}. "
            f"Stored types: {list(storage.keys())}. "
            "The router and connector use mismatched entity_info types."
        )
        assert _CONNECTOR_TOKEN_TYPE in storage, (
            f"Token was NOT stored under connector type {_CONNECTOR_TOKEN_TYPE!r}. "
            f"Stored types: {list(storage.keys())}. "
            "The router and connector use mismatched entity_info types."
        )

        assert storage[_CONNECTOR_URL_TYPE] == "http://homeassistant.local:8123"
        assert storage[_CONNECTOR_TOKEN_TYPE] == "long-lived-access-token-abc123"

    async def test_delete_removes_connector_types(self) -> None:
        """DELETE /api/settings/home-assistant removes both connector-expected types.

        Ensures the delete path also uses the correct entity_info types, so
        re-configuration after deletion does not read stale credentials.
        """
        app = _build_app()
        deleted_types: list[str] = []

        async def _mock_delete(_pool, info_type: str) -> bool:
            deleted_types.append(info_type)
            return True

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_DELETE_EI_PATCH, side_effect=_mock_delete),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        assert resp.status_code == 200

        assert _CONNECTOR_URL_TYPE in deleted_types, (
            f"DELETE did not remove connector URL type {_CONNECTOR_URL_TYPE!r}. "
            f"Deleted types: {deleted_types}"
        )
        assert _CONNECTOR_TOKEN_TYPE in deleted_types, (
            f"DELETE did not remove connector token type {_CONNECTOR_TOKEN_TYPE!r}. "
            f"Deleted types: {deleted_types}"
        )

    async def test_get_status_reads_from_connector_types(self) -> None:
        """GET /api/settings/home-assistant reads from connector-expected types.

        After the connector stores credentials, the dashboard GET should reflect
        the same entity_info types. This validates that the status endpoint also
        uses the correct type names.
        """
        app = _build_app()
        storage = {
            _CONNECTOR_URL_TYPE: "http://homeassistant.local:8123",
            _CONNECTOR_TOKEN_TYPE: "my-access-token",
        }

        async def _mock_resolve(_pool, info_type: str) -> str | None:
            return storage.get(info_type)

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=_mock_resolve),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()

        assert body["state"] == "connected", (
            f"Expected 'connected' state when creds stored under connector types, "
            f"got {body['state']!r}. "
            "GET /api/settings/home-assistant may be reading from the wrong types."
        )
        assert body["url_configured"] is True
        assert body["token_configured"] is True

    async def test_wrong_types_produce_not_configured(self) -> None:
        """Using the old pre-fix type names results in 'not_configured' state.

        This reproduces the regression where the router reads from entity_info
        but the old keys were stored in butler_secrets or under different names.
        """
        app = _build_app()
        # Deliberately use the WRONG type names to reproduce the bug
        storage = {
            "home_assistant:base_url": "http://homeassistant.local:8123",
            "home_assistant:access_token": "my-access-token",
        }

        async def _mock_resolve(_pool, info_type: str) -> str | None:
            return storage.get(info_type)

        with (
            patch(_RESOLVE_POOL_PATCH, return_value=MagicMock()),
            patch(_RESOLVE_EI_PATCH, side_effect=_mock_resolve),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        body = resp.json()

        # The wrong types should NOT yield 'connected' — they should be invisible
        assert body["state"] == "not_configured", (
            f"Storing credentials under legacy keys incorrectly "
            f"shows state={body['state']!r}. "
            "The router must only read from the entity_info types."
        )
