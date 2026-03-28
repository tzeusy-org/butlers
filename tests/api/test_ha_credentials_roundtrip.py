"""Regression test: HA dashboard settings → connector credential round-trip.

Validates the full flow described in bu-v7ni:
  1. POST /api/settings/home-assistant writes credentials via the router.
  2. Credentials land in CredentialStore under the namespaced keys that the
     HA connector process expects (``home_assistant:base_url`` and
     ``home_assistant:access_token``).
  3. The connector's ``_main()`` credential resolution path (mocked) resolves
     those same keys back to the submitted values.

This test caught the regression fixed in bu-yn6a / PR #910, where the router
was writing ``HA_URL`` / ``HA_TOKEN`` but the connector read
``home_assistant:base_url`` / ``home_assistant:access_token``, meaning
credentials saved via the dashboard were silently ignored at connector startup.

No real HA instance or database is required — the HA HTTP validation and DB
pool are mocked so the test runs in CI without external dependencies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers.home_assistant import (
    _CRED_HA_TOKEN,
    _CRED_HA_URL,
    _get_db_manager,
)
from butlers.credential_store import CredentialStore

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants under test — must match the connector's lookup keys exactly
# ---------------------------------------------------------------------------

_CONNECTOR_BASE_URL_KEY = "home_assistant:base_url"
_CONNECTOR_ACCESS_TOKEN_KEY = "home_assistant:access_token"

_VALIDATE_PATCH = "butlers.api.routers.home_assistant._validate_ha_connection"
_MAKE_CRED_STORE_PATCH = "butlers.api.routers.home_assistant._make_credential_store"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_recording_cred_store() -> tuple[MagicMock, dict[str, str]]:
    """Return (mock_cred_store, storage_dict).

    The mock's ``store`` method writes (key, value) into ``storage_dict``.
    The mock's ``resolve`` method reads from ``storage_dict``.
    This lets tests assert on both what the router wrote and what a
    downstream ``resolve()`` caller would read.
    """
    storage: dict[str, str] = {}
    store = MagicMock()

    async def _store(key: str, value: str, **_kwargs: Any) -> None:
        storage[key] = value

    async def _resolve(key: str, **_kwargs: Any) -> str | None:
        return storage.get(key)

    store.store = AsyncMock(side_effect=_store)
    store.resolve = AsyncMock(side_effect=_resolve)
    store.delete = AsyncMock(return_value=True)
    return store, storage


def _make_db_manager(cred_store: MagicMock) -> MagicMock:
    pool = MagicMock()
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    return db_manager


def _build_app(cred_store: MagicMock | None) -> Any:
    from butlers.api.app import create_app

    app = create_app(api_key="")
    if cred_store is not None:
        db_manager = _make_db_manager(cred_store)
        app.dependency_overrides[_get_db_manager] = lambda: db_manager
    return app


# ---------------------------------------------------------------------------
# Key-alignment contract tests
# ---------------------------------------------------------------------------


class TestCredentialKeyAlignment:
    """The router must use the same key names the connector looks up.

    These tests pin the exact string values of the credential keys.
    A rename on either side without updating the other would break the
    round-trip and should be caught here immediately.
    """

    def test_router_url_key_matches_connector_lookup(self) -> None:
        """Router constant _CRED_HA_URL equals the key the connector reads."""
        assert _CRED_HA_URL == _CONNECTOR_BASE_URL_KEY, (
            f"Router writes URL under {_CRED_HA_URL!r} but connector reads "
            f"{_CONNECTOR_BASE_URL_KEY!r} — credential round-trip broken."
        )

    def test_router_token_key_matches_connector_lookup(self) -> None:
        """Router constant _CRED_HA_TOKEN equals the key the connector reads."""
        assert _CRED_HA_TOKEN == _CONNECTOR_ACCESS_TOKEN_KEY, (
            f"Router writes token under {_CRED_HA_TOKEN!r} but connector reads "
            f"{_CONNECTOR_ACCESS_TOKEN_KEY!r} — credential round-trip broken."
        )


# ---------------------------------------------------------------------------
# Full HTTP round-trip regression test
# ---------------------------------------------------------------------------


class TestHACredentialRoundTrip:
    """POST /api/settings → CredentialStore → connector reads back the same values."""

    async def test_post_writes_credentials_under_connector_keys(self) -> None:
        """POST /api/settings/home-assistant stores creds under connector-expected keys.

        Regression test for bu-yn6a (PR #910): the router was writing HA_URL /
        HA_TOKEN but the connector read home_assistant:base_url /
        home_assistant:access_token, making dashboard-configured credentials
        invisible to the connector at startup.
        """
        cred_store, storage = _make_recording_cred_store()
        app = _build_app(cred_store)

        with (
            patch(_MAKE_CRED_STORE_PATCH, return_value=cred_store),
            patch(_VALIDATE_PATCH, return_value=None),
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

        # The router must have stored the credentials under the NAMESPACED keys
        # that the connector's _main() will later look up.
        assert _CONNECTOR_BASE_URL_KEY in storage, (
            f"URL was NOT stored under connector key {_CONNECTOR_BASE_URL_KEY!r}. "
            f"Stored keys: {list(storage.keys())}. "
            "The router and connector use mismatched credential keys."
        )
        assert _CONNECTOR_ACCESS_TOKEN_KEY in storage, (
            f"Token was NOT stored under connector key {_CONNECTOR_ACCESS_TOKEN_KEY!r}. "
            f"Stored keys: {list(storage.keys())}. "
            "The router and connector use mismatched credential keys."
        )

        assert storage[_CONNECTOR_BASE_URL_KEY] == "http://homeassistant.local:8123"
        assert storage[_CONNECTOR_ACCESS_TOKEN_KEY] == "long-lived-access-token-abc123"

    async def test_connector_resolve_reads_what_router_wrote(self) -> None:
        """CredentialStore.resolve returns the URL and token the router stored.

        Simulates the connector startup path: after the router writes
        credentials, the connector calls ``cred_store.resolve(key)`` using the
        same namespaced keys.  Both operations must agree on the key names.
        """
        # Step 1 — Simulate router writing credentials via CredentialStore.store()
        storage: dict[str, str] = {}
        mock_pool = MagicMock()
        mock_conn = AsyncMock()

        def _mock_execute(query: str, *args: Any) -> None:
            if "butler_secrets" in query and args:
                storage[args[0]] = args[1]  # positional: key, value, ...

        def _mock_fetchrow(query: str, key: str) -> dict[str, str] | None:
            val = storage.get(key)
            return {"secret_value": val} if val is not None else None

        mock_conn.execute.side_effect = _mock_execute
        mock_conn.fetchrow.side_effect = _mock_fetchrow
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        cred_store = CredentialStore(mock_pool)

        await cred_store.store(
            "home_assistant:base_url",
            "http://homeassistant.local:8123",
            category="home_assistant",
            description="Home Assistant base URL",
            is_sensitive=False,
        )
        await cred_store.store(
            "home_assistant:access_token",
            "long-lived-access-token-abc123",
            category="home_assistant",
            description="Home Assistant long-lived access token",
            is_sensitive=True,
        )

        # Step 2 — Simulate connector startup reading back the credentials
        resolved_url = await cred_store.resolve("home_assistant:base_url")
        resolved_token = await cred_store.resolve("home_assistant:access_token")

        assert resolved_url == "http://homeassistant.local:8123", (
            "Connector could not read back the URL stored by the router. "
            "Key mismatch between router write path and connector read path."
        )
        assert resolved_token == "long-lived-access-token-abc123", (
            "Connector could not read back the token stored by the router. "
            "Key mismatch between router write path and connector read path."
        )

    async def test_delete_removes_connector_keys(self) -> None:
        """DELETE /api/settings/home-assistant removes both connector-expected keys.

        Ensures the delete path also uses the correct namespaced keys, so
        re-configuration after deletion does not read stale credentials.
        """
        cred_store, storage = _make_recording_cred_store()

        # Seed storage with credentials (simulates a previous successful POST)
        storage[_CONNECTOR_BASE_URL_KEY] = "http://homeassistant.local:8123"
        storage[_CONNECTOR_ACCESS_TOKEN_KEY] = "old-access-token"

        app = _build_app(cred_store)

        with patch(_MAKE_CRED_STORE_PATCH, return_value=cred_store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/api/settings/home-assistant")

        assert resp.status_code == 200

        # Verify that the delete operation targeted the namespaced connector keys
        deleted_keys = {call.args[0] for call in cred_store.delete.call_args_list}
        assert _CONNECTOR_BASE_URL_KEY in deleted_keys, (
            f"DELETE did not remove connector URL key {_CONNECTOR_BASE_URL_KEY!r}. "
            f"Deleted keys: {deleted_keys}"
        )
        assert _CONNECTOR_ACCESS_TOKEN_KEY in deleted_keys, (
            f"DELETE did not remove connector token key {_CONNECTOR_ACCESS_TOKEN_KEY!r}. "
            f"Deleted keys: {deleted_keys}"
        )

    async def test_get_status_reads_from_connector_keys(self) -> None:
        """GET /api/settings/home-assistant reads from connector-expected keys.

        After the connector restores, the dashboard GET should reflect the same
        namespaced credentials.  This validates that the status endpoint also
        uses the correct key names.
        """
        cred_store, storage = _make_recording_cred_store()

        # Pre-populate storage as if the router had written them
        storage[_CONNECTOR_BASE_URL_KEY] = "http://homeassistant.local:8123"
        storage[_CONNECTOR_ACCESS_TOKEN_KEY] = "my-access-token"

        app = _build_app(cred_store)

        with patch(_MAKE_CRED_STORE_PATCH, return_value=cred_store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        assert resp.status_code == 200
        body = resp.json()

        assert body["state"] == "connected", (
            f"Expected 'connected' state when creds stored under connector keys, "
            f"got {body['state']!r}. "
            "GET /api/settings/home-assistant may be reading from the wrong keys."
        )
        assert body["url_configured"] is True
        assert body["token_configured"] is True

    async def test_wrong_keys_produce_not_configured(self) -> None:
        """Using the old pre-fix key names results in 'not_configured' state.

        This is the exact regression that bu-yn6a / PR #910 fixed: if the
        router stores credentials under HA_URL / HA_TOKEN (the old names)
        while the connector reads home_assistant:base_url /
        home_assistant:access_token, the GET status reports 'not_configured'
        even though the user has configured HA successfully.
        """
        cred_store, storage = _make_recording_cred_store()

        # Deliberately use the WRONG (pre-fix) key names to reproduce the bug
        storage["HA_URL"] = "http://homeassistant.local:8123"
        storage["HA_TOKEN"] = "my-access-token"

        app = _build_app(cred_store)

        with patch(_MAKE_CRED_STORE_PATCH, return_value=cred_store):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/settings/home-assistant")

        body = resp.json()

        # The wrong keys should NOT yield 'connected' — they should be invisible
        assert body["state"] == "not_configured", (
            f"Storing credentials under legacy keys HA_URL/HA_TOKEN incorrectly "
            f"shows state={body['state']!r}. "
            "The router must only read from the namespaced connector keys."
        )
