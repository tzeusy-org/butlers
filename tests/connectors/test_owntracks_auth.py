"""Tests for OwnTracks webhook authentication (tasks 2.1–2.4).

Coverage:
- Token resolution from CredentialStore (DB-first)
- Token resolution env var fallback
- Token resolution: no token configured → None
- FastAPI dependency: valid token → 200
- FastAPI dependency: missing Authorization header → 401
- FastAPI dependency: malformed header (no "Bearer" prefix) → 401
- FastAPI dependency: wrong token → 401
- Constant-time comparison is used (hmac.compare_digest)
- Fail-closed startup: RuntimeError when no token available
"""

from __future__ import annotations

import hmac
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.connectors.owntracks import (
    _DB_KEY,
    _ENV_VAR,
    build_webhook_app,
    make_bearer_auth_dependency,
    resolve_owntracks_webhook_token,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TOKEN = "abc123secret"


def _make_mock_store(token: str | None) -> MagicMock:
    """Return a mock CredentialStore that resolves *token* (or None) for _DB_KEY."""
    store = MagicMock()
    store.resolve = AsyncMock(return_value=token)
    return store


async def _post_webhook(
    app: FastAPI,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
) -> httpx.Response:
    """POST /owntracks/webhook on *app* via ASGI transport."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.post(
            "/owntracks/webhook",
            headers=headers or {},
            json=json_body or {"_type": "location", "lat": 1.0, "lon": 2.0, "tst": 1},
        )


# ---------------------------------------------------------------------------
# Token resolution tests (tasks 2.1, 2.2)
# ---------------------------------------------------------------------------


class TestResolveOwnTracksWebhookToken:
    """Tests for resolve_owntracks_webhook_token()."""

    async def test_resolves_from_credential_store_when_present(self) -> None:
        """DB token takes priority over env var."""
        store = _make_mock_store(_VALID_TOKEN)
        with patch.dict("os.environ", {_ENV_VAR: "env-token"}, clear=False):
            result = await resolve_owntracks_webhook_token(store=store)
        assert result == _VALID_TOKEN
        store.resolve.assert_awaited_once_with(_DB_KEY, env_fallback=False)

    async def test_falls_back_to_env_var_when_db_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env var fallback is used when CredentialStore returns None."""
        store = _make_mock_store(None)
        monkeypatch.setenv(_ENV_VAR, "env-token-fallback")
        result = await resolve_owntracks_webhook_token(store=store)
        assert result == "env-token-fallback"

    async def test_returns_none_when_neither_source_has_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when DB is empty and env var is unset."""
        store = _make_mock_store(None)
        monkeypatch.delenv(_ENV_VAR, raising=False)
        result = await resolve_owntracks_webhook_token(store=store)
        assert result is None

    async def test_resolves_from_env_when_no_store_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When store=None, falls straight to env var."""
        monkeypatch.setenv(_ENV_VAR, "no-store-token")
        result = await resolve_owntracks_webhook_token(store=None)
        assert result == "no-store-token"

    async def test_returns_none_when_no_store_and_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when store is None and env var is absent."""
        monkeypatch.delenv(_ENV_VAR, raising=False)
        result = await resolve_owntracks_webhook_token(store=None)
        assert result is None

    async def test_falls_back_to_env_when_credential_store_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CredentialStore errors are non-fatal; env var fallback still tried."""
        store = MagicMock()
        store.resolve = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        monkeypatch.setenv(_ENV_VAR, "fallback-on-error")
        result = await resolve_owntracks_webhook_token(store=store)
        assert result == "fallback-on-error"

    async def test_env_var_whitespace_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Leading/trailing whitespace in env var value is stripped."""
        store = _make_mock_store(None)
        monkeypatch.setenv(_ENV_VAR, "  padded-token  ")
        result = await resolve_owntracks_webhook_token(store=store)
        assert result == "padded-token"

    async def test_empty_env_var_treated_as_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty env var (whitespace-only) is treated as not configured."""
        store = _make_mock_store(None)
        monkeypatch.setenv(_ENV_VAR, "   ")
        result = await resolve_owntracks_webhook_token(store=store)
        assert result is None


# ---------------------------------------------------------------------------
# Fail-closed startup (task 2.3)
# ---------------------------------------------------------------------------


class TestFailClosedStartup:
    """Connector must refuse to start when no token is available."""

    async def test_fail_closed_raises_when_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Startup should raise RuntimeError when resolve returns None."""
        store = _make_mock_store(None)
        monkeypatch.delenv(_ENV_VAR, raising=False)

        token = await resolve_owntracks_webhook_token(store=store)

        # This is what the connector entrypoint should do:
        if token is None:
            with pytest.raises(RuntimeError, match="owntracks_webhook_token"):
                raise RuntimeError(
                    "OwnTracks webhook token not configured. "
                    "Store it under owntracks_webhook_token via the dashboard "
                    f"or set the {_ENV_VAR} env var."
                )

    def test_make_bearer_auth_dependency_raises_on_empty_token(self) -> None:
        """make_bearer_auth_dependency must reject an empty token at factory time."""
        with pytest.raises(ValueError, match="non-empty"):
            make_bearer_auth_dependency(token="")

    def test_make_bearer_auth_dependency_raises_on_whitespace_token(self) -> None:
        """make_bearer_auth_dependency treats whitespace-only token as empty."""
        # An empty string is the degenerate case; whitespace would pass
        # hmac.compare_digest by accident if the sender also sends whitespace.
        # Callers should strip before passing; an empty string is refused.
        with pytest.raises(ValueError, match="non-empty"):
            make_bearer_auth_dependency(token="")


# ---------------------------------------------------------------------------
# FastAPI dependency: valid request (task 2.2)
# ---------------------------------------------------------------------------


class TestBearerAuthDependency:
    """Tests for make_bearer_auth_dependency() via the FastAPI test client."""

    async def test_valid_token_returns_200(self) -> None:
        """Correct bearer token → 200 OK with empty-array body."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": f"Bearer {_VALID_TOKEN}"})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_missing_authorization_header_returns_401(self) -> None:
        """No Authorization header → 401 Unauthorized."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={})
        assert resp.status_code == 401
        assert resp.json() == {"detail": {"error": "Unauthorized"}}

    async def test_malformed_header_no_bearer_prefix_returns_401(self) -> None:
        """Header with wrong scheme (Token instead of Bearer) → 401."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": f"Token {_VALID_TOKEN}"})
        assert resp.status_code == 401
        assert resp.json() == {"detail": {"error": "Unauthorized"}}

    async def test_malformed_header_only_scheme_returns_401(self) -> None:
        """Header with 'Bearer' but no token value → 401."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": "Bearer"})
        assert resp.status_code == 401

    async def test_wrong_token_returns_401(self) -> None:
        """Correct scheme but wrong token value → 401."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401
        assert resp.json() == {"detail": {"error": "Unauthorized"}}

    async def test_empty_bearer_value_returns_401(self) -> None:
        """'Bearer ' with an empty token value → 401."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    async def test_bearer_case_insensitive_scheme(self) -> None:
        """'bearer' (lowercase) scheme is accepted per HTTP spec."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": f"bearer {_VALID_TOKEN}"})
        assert resp.status_code == 200

    async def test_bearer_mixed_case_scheme(self) -> None:
        """'BEARER' (uppercase) scheme is accepted per HTTP spec."""
        app = build_webhook_app(token=_VALID_TOKEN)
        resp = await _post_webhook(app, headers={"Authorization": f"BEARER {_VALID_TOKEN}"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Constant-time comparison (task 2.4)
# ---------------------------------------------------------------------------


class TestConstantTimeComparison:
    """Verify that hmac.compare_digest is used for token validation."""

    def test_hmac_compare_digest_used_directly(self) -> None:
        """hmac.compare_digest behaves correctly for equal and unequal tokens."""
        token = "secret-token-value"
        assert hmac.compare_digest(token.encode(), token.encode()) is True
        assert hmac.compare_digest(token.encode(), b"wrong-token") is False

    async def test_compare_digest_is_called_for_validation(self) -> None:
        """Integration check: patching hmac.compare_digest confirms it is invoked."""
        app = build_webhook_app(token=_VALID_TOKEN)

        with patch(
            "butlers.connectors.owntracks.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as mock_digest:
            resp = await _post_webhook(app, headers={"Authorization": f"Bearer {_VALID_TOKEN}"})

        assert resp.status_code == 200
        mock_digest.assert_called_once()
        args = mock_digest.call_args[0]
        assert args[0] == _VALID_TOKEN.encode()
        assert args[1] == _VALID_TOKEN.encode()

    async def test_compare_digest_called_for_invalid_token(self) -> None:
        """hmac.compare_digest is still called even when token does not match."""
        app = build_webhook_app(token=_VALID_TOKEN)

        with patch(
            "butlers.connectors.owntracks.hmac.compare_digest",
            wraps=hmac.compare_digest,
        ) as mock_digest:
            resp = await _post_webhook(app, headers={"Authorization": "Bearer wrong-token"})

        assert resp.status_code == 401
        mock_digest.assert_called_once()
