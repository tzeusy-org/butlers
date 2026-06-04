"""Unit tests for the OAuth revoke handler registry (bu-qj3e1 refactor).

Validates:
(a) A registered provider's handler is invoked and its status is returned.
(b) An unregistered provider returns "skipped".
(c) A non-OAuth credential type returns "skipped" without consulting the registry.
(d) Google still succeeds on mocked HTTP 200 and reports "failed:" on non-200.

All tests are pure unit tests — no real network calls are made; httpx is fully mocked.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.routers.secrets_v2 import (
    _revoke_handler_registry,
    _revoke_oauth_token,
    register_revoke_handler,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolated_registry(monkeypatch) -> dict:
    """Return a fresh copy of the registry and patch it in for the duration of the test.

    This prevents test-registered handlers from leaking into each other and avoids
    accidentally un-registering the real google/github handlers in the production registry.
    """
    fresh = copy.copy(_revoke_handler_registry)
    monkeypatch.setattr(
        "butlers.api.routers.secrets_v2._revoke_handler_registry",
        fresh,
    )
    return fresh


# ---------------------------------------------------------------------------
# (a) Registered provider handler is invoked and status returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_handler_is_called_and_status_returned(monkeypatch):
    """A handler registered for a provider is awaited and its return value is passed through."""
    registry = _isolated_registry(monkeypatch)

    called_with: list[tuple] = []

    async def _fake_handler(old_value: str, *, shared_pool) -> str:
        called_with.append((old_value, shared_pool))
        return "succeeded"

    registry["myprovider"] = _fake_handler

    result = await _revoke_oauth_token(
        provider="myprovider",
        credential_type="myprovider_oauth_refresh",
        old_value="tok-abc",
        shared_pool=None,
    )

    assert result == "succeeded"
    assert called_with == [("tok-abc", None)], f"Handler called with unexpected args: {called_with}"


@pytest.mark.asyncio
async def test_registered_handler_failed_status_is_passed_through(monkeypatch):
    """A handler returning 'failed:...' has that value forwarded to the caller."""
    registry = _isolated_registry(monkeypatch)

    async def _failing_handler(old_value: str, *, shared_pool) -> str:
        return "failed:HTTP 403"

    registry["myprovider"] = _failing_handler

    result = await _revoke_oauth_token(
        provider="myprovider",
        credential_type="myprovider_oauth_access",
        old_value="tok-xyz",
        shared_pool=None,
    )

    assert result == "failed:HTTP 403"


# ---------------------------------------------------------------------------
# (b) Unregistered provider returns "skipped"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unregistered_provider_returns_skipped(monkeypatch):
    """A provider not in the registry causes _revoke_oauth_token to return 'skipped'."""
    _isolated_registry(monkeypatch)

    # "noprovider" is deliberately absent from the isolated registry.
    result = await _revoke_oauth_token(
        provider="noprovider",
        credential_type="noprovider_oauth_refresh",
        old_value="tok",
        shared_pool=None,
    )

    assert result == "skipped"


# ---------------------------------------------------------------------------
# (c) Non-OAuth credential type returns "skipped" without consulting the registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_oauth_credential_type_skips_registry(monkeypatch):
    """A credential whose type does not end with _oauth_refresh or _oauth_access is skipped
    immediately — the registry is never consulted."""
    registry = _isolated_registry(monkeypatch)

    handler_called = False

    async def _spy_handler(old_value: str, *, shared_pool) -> str:
        nonlocal handler_called
        handler_called = True
        return "succeeded"

    registry["spotify"] = _spy_handler

    result = await _revoke_oauth_token(
        provider="spotify",
        credential_type="spotify_api_key",  # NOT an OAuth type
        old_value="api-key-val",
        shared_pool=None,
    )

    assert result == "skipped"
    assert not handler_called, "Registry handler must NOT be called for non-OAuth credential types"


# ---------------------------------------------------------------------------
# (d) Google succeeds on HTTP 200 and reports "failed:" on non-200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_revoke_http_200_returns_succeeded(monkeypatch):
    """Google's registered handler returns 'succeeded' when the revoke endpoint returns 200."""

    async def _fake_post(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 200
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    # Use the real (unpatched) registry so google handler is present.
    result = await _revoke_oauth_token(
        provider="google",
        credential_type="google_oauth_refresh",
        old_value="my-google-token",
        shared_pool=None,
    )

    assert result == "succeeded"
    fake_client.post.assert_called_once()
    call_url = str(fake_client.post.call_args[0][0])
    assert "oauth2.googleapis.com/revoke" in call_url


@pytest.mark.asyncio
async def test_google_revoke_http_non_200_returns_failed(monkeypatch):
    """Google's registered handler returns 'failed:HTTP <code>' on non-200 responses."""

    async def _fake_post(url, **kwargs):
        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 400
        return fake_resp

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    result = await _revoke_oauth_token(
        provider="google",
        credential_type="google_oauth_refresh",
        old_value="stale-token",
        shared_pool=None,
    )

    assert result == "failed:HTTP 400"


@pytest.mark.asyncio
async def test_google_revoke_network_error_returns_failed(monkeypatch):
    """Google's registered handler returns 'failed:<ExcType>' on network errors."""

    async def _fake_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    result = await _revoke_oauth_token(
        provider="google",
        credential_type="google_oauth_access",
        old_value="stale-token",
        shared_pool=None,
    )

    assert result == "failed:ConnectError"


# ---------------------------------------------------------------------------
# register_revoke_handler API shape tests
# ---------------------------------------------------------------------------


def test_register_revoke_handler_decorator_registers_and_returns_fn(monkeypatch):
    """@register_revoke_handler returns the original function unchanged."""
    registry = _isolated_registry(monkeypatch)

    @register_revoke_handler("testprovider")
    async def _my_handler(old_value: str, *, shared_pool) -> str:
        return "succeeded"

    assert registry.get("testprovider") is _my_handler


def test_register_revoke_handler_direct_call_registers_fn(monkeypatch):
    """register_revoke_handler(provider, fn) registers without decorator syntax."""
    registry = _isolated_registry(monkeypatch)

    async def _my_handler(old_value: str, *, shared_pool) -> str:
        return "succeeded"

    returned = register_revoke_handler("testprovider2", _my_handler)
    assert registry.get("testprovider2") is _my_handler
    assert returned is _my_handler
