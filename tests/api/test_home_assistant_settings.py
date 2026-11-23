"""HA settings probe retains its status policy and caller-owned HTTPX lifetime."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from butlers import home_assistant_client as ha
from butlers.api.routers import home_assistant as api

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        (200, None),
        (401, "auth_failure"),
        (403, "auth_failure"),
        (500, "unexpected"),
        ("timeout", "unreachable"),
        ("connection", "unreachable"),
        ("cancel", "cancel"),
    ],
)
async def test_probe_keeps_status_mapping_and_closes_shared_client(monkeypatch, outcome, category):
    constructor = httpx.AsyncClient
    clients, kwargs_seen, requests = [], [], []

    async def handler(request):
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic-timeout")
        if outcome == "connection":
            raise httpx.ConnectError("synthetic-connection")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return httpx.Response(outcome, json={})

    def construct(**kwargs):
        kwargs_seen.append(dict(kwargs))
        client = constructor(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    monkeypatch.setattr(ha.httpx, "AsyncClient", construct)
    if category is None:
        await api._validate_ha_connection("https://ha.test/", "synthetic-token")
    elif category == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await api._validate_ha_connection("https://ha.test/", "synthetic-token")
    else:
        with pytest.raises(api._HAValidationError) as caught:
            await api._validate_ha_connection("https://ha.test/", "synthetic-token")
        assert caught.value.category == category
    assert len(requests) == 1 and requests[0].url.path == "/api/"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-token"
    assert clients[0].is_closed
    assert kwargs_seen[0]["timeout"] == 10.0 and kwargs_seen[0]["verify"] is True
