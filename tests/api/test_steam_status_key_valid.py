"""Tests for the Steam account-status ``key_valid`` probe.

The status endpoint must report ``key_valid`` from a *real* lightweight Steam
Web API test call (not a hardcoded ``None``):

- HTTP 200            -> ``True``  (key authenticated)
- HTTP 401 / 403      -> ``False`` (Steam rejected the key)
- network / transient -> ``None``  (unknown — never falsely report invalid)

These tests drive ``_check_api_key_valid`` with a mocked ``SteamAPIClient`` so
no real network call is made, and assert the verdict mapping plus that the API
key never leaks into the verdict.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

import butlers.api.routers.steam as steam_router
from butlers.steam.client import SteamAPIError, SteamRateLimitError

_STEAM_ID = 76561198000000000
_API_KEY = "SECRET_STEAM_KEY_should_not_leak"


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, *, response: Any = None, error: Exception | None = None
) -> None:
    """Patch ``SteamAPIClient`` in the router module with a fake async client.

    The fake mimics ``async with SteamAPIClient(api_key=...) as client`` and a
    single ``await client.request(...)`` call, returning ``response`` or raising
    ``error``.
    """

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def request(self, *args: Any, **kwargs: Any) -> Any:
            if error is not None:
                raise error
            return response

    @asynccontextmanager
    async def _factory(*args: Any, **kwargs: Any):
        yield _FakeClient()

    # SteamAPIClient(...) is used as an async context manager; replace it with a
    # callable that returns one.
    monkeypatch.setattr(steam_router, "SteamAPIClient", _factory)


async def test_key_valid_true_on_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(
        monkeypatch,
        response={"players": [{"steamid": str(_STEAM_ID), "personaname": "Tester"}]},
    )

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is True


async def test_key_invalid_false_on_http_401(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, error=SteamAPIError(status_code=401, body="unauthorized"))

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is False


async def test_key_invalid_false_on_http_403(monkeypatch: pytest.MonkeyPatch) -> None:
    # The client raises SteamRateLimitError for both 403 and 429; 403 means a
    # bad/unauthorized key and must map to False.
    _patch_client(monkeypatch, error=SteamRateLimitError(retry_after_s=60.0, status_code=403))

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is False


async def test_key_unknown_none_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, error=ConnectionError("connection refused"))

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is None


async def test_key_unknown_none_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Genuine rate-limiting (429) is transient, not an auth failure -> unknown.
    _patch_client(monkeypatch, error=SteamRateLimitError(retry_after_s=120.0, status_code=429))

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is None


async def test_key_valid_true_when_auth_ok_but_no_players(monkeypatch: pytest.MonkeyPatch) -> None:
    # HTTP 200 with an empty players list: auth succeeded, so the key is valid.
    _patch_client(monkeypatch, response={"players": []})

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    assert result is True


async def test_api_key_does_not_leak_into_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, error=SteamAPIError(status_code=401, body="unauthorized"))

    result = await steam_router._check_api_key_valid(_API_KEY, _STEAM_ID)

    # The verdict is a bare tri-state; the key must never be embedded in it.
    assert result in (True, False, None)
    assert _API_KEY not in repr(result)
