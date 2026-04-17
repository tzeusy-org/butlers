"""Tests for :mod:`butlers.steam.client` — focused on the async client lifecycle."""

from __future__ import annotations

import httpx
import pytest

from butlers.steam.client import SteamAPIClient, _make_request_redact_hook


async def test_redact_hook_is_awaitable() -> None:
    """The httpx AsyncClient awaits every request hook, so the hook must be async.

    Regression guard: when the hook was a plain sync function, every Steam API
    call failed with ``TypeError: object NoneType can't be used in 'await' expression``.
    """
    hook = _make_request_redact_hook("secret_key")
    request = httpx.Request("GET", "https://example.invalid/?key=secret_key")
    # Calling an async function returns a coroutine we can await.
    coro = hook(request)
    assert coro is not None
    await coro
    assert dict(request.url.params)["key"] == "[REDACTED]"


async def test_request_does_not_raise_await_noneType(httpx_mock_ok) -> None:  # noqa: N802
    """End-to-end guard: a real SteamAPIClient.request() call must succeed without the redact-hook error."""
    async with SteamAPIClient(api_key="abc") as client:
        data = await client.request("ISteamUser", "GetPlayerSummaries")
    assert data == {"players": []}


@pytest.fixture
def httpx_mock_ok(monkeypatch: pytest.MonkeyPatch):
    """Patch ``httpx.AsyncClient.get`` to return a canned Steam-shaped response."""

    async def _fake_get(self, url, params=None, **kwargs):  # noqa: ANN001
        return httpx.Response(
            status_code=200,
            json={"response": {"players": []}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    return None
