"""Tests for :mod:`butlers.steam.client` — focused on the async client lifecycle."""

from __future__ import annotations

import logging

import httpx
import pytest

from butlers.steam.client import _REDACTED, SteamAPIClient, _APIKeyRedactingFilter


async def test_outgoing_request_preserves_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the client must send the REAL api key to Steam.

    Previously an httpx request event hook replaced ``key=<api_key>`` with
    ``key=[REDACTED]`` in the outgoing URL itself — Steam then returned HTTP
    403 and the dashboard surfaced "Steam API key is invalid or unauthorized".
    """
    captured: dict[str, str] = {}

    async def _capture_get(self, url, params=None, **kwargs):  # noqa: ANN001
        captured["key"] = (params or {}).get("key", "")
        return httpx.Response(
            status_code=200,
            json={"response": {"players": []}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _capture_get)

    async with SteamAPIClient(api_key="my_real_key_123") as client:
        await client.request("ISteamUser", "GetPlayerSummaries")

    assert captured["key"] == "my_real_key_123", (
        "The redact hook must not mutate the outgoing request — Steam rejects "
        "a redacted key with HTTP 403."
    )


async def test_httpx_log_records_are_redacted(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API key must never appear in log records emitted by the ``httpx`` logger."""

    async def _fake_get(self, url, params=None, **kwargs):  # noqa: ANN001
        # Emit a log line on the httpx logger the same way httpx would —
        # containing the query string with the key.
        logging.getLogger("httpx").info(
            'HTTP Request: GET %s "HTTP/1.1 200 OK"', f"{url}?key=secret_abc"
        )
        return httpx.Response(
            status_code=200,
            json={"response": {"players": []}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    with caplog.at_level(logging.INFO, logger="httpx"):
        async with SteamAPIClient(api_key="secret_abc") as client:
            await client.request("ISteamUser", "GetPlayerSummaries")

    formatted = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret_abc" not in formatted
    assert _REDACTED in formatted


async def test_filter_is_removed_on_close() -> None:
    """Opening then closing the client must leave the httpx logger clean."""
    httpx_logger = logging.getLogger("httpx")
    before = {id(f) for f in httpx_logger.filters}

    client = SteamAPIClient(api_key="abc")
    await client.open()
    assert any(isinstance(f, _APIKeyRedactingFilter) for f in httpx_logger.filters)
    await client.close()

    after = {id(f) for f in httpx_logger.filters}
    assert after == before
