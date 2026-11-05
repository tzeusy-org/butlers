"""HA shared transport contracts; all IO is synthetic and owner lifetimes are real."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers import home_assistant_client as ha
from butlers.connectors import home_assistant_rest as rest
from butlers.connectors.home_assistant import HAWebSocketClient
from butlers.modules._roster_home import HomeAssistantConfig, HomeAssistantModule

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://ha.test/", "ws://ha.test/api/websocket"),
        ("https://ha.test/prefix", "wss://ha.test/prefix/api/websocket"),
        ("ws://ha.test", "ws://ha.test/api/websocket"),
        ("wss://ha.test/", "wss://ha.test/api/websocket"),
        ("ftp://ha.test", None),
        ("file:///tmp/private", None),
        ("ha.test", None),
    ],
)
def test_ws_url_admits_only_supported_schemes(url, expected):
    if expected is None:
        with pytest.raises(ValueError, match="Unsupported HA URL"):
            ha.ws_url(url)
    else:
        assert ha.ws_url(url) == expected


@pytest.mark.parametrize("owner", ["connector", "module", "statistics"])
@pytest.mark.parametrize("outcome", ["ok", "invalid", "greeting", "response", "timeout", "cancel"])
async def test_all_ws_owners_share_handshake_and_own_connection_lifetimes(
    monkeypatch, owner, outcome
):
    greeting = {"type": "auth_required"}
    response = {"type": "auth_ok", "ha_version": "synthetic"}
    if outcome == "greeting":
        greeting = ["SYNTHETIC-PRIVATE-PROVIDER"]
    if outcome == "response":
        response = ["SYNTHETIC-PRIVATE-PROVIDER"]
    if outcome == "invalid":
        response = {"type": "auth_invalid", "message": "SYNTHETIC-PRIVATE-PROVIDER"}
    responses = [greeting, response]
    if outcome == "timeout":
        responses[1] = TimeoutError("SYNTHETIC-PRIVATE-PROVIDER")
    if outcome == "cancel":
        responses[1] = asyncio.CancelledError()
    ws = SimpleNamespace(
        closed=False,
        receive_json=AsyncMock(
            side_effect=responses
            + [{"id": 1, "type": "result", "success": True, "result": {"sensor.synthetic": []}}]
        ),
        send_json=AsyncMock(),
        close=AsyncMock(),
    )
    session = MagicMock(closed=False)
    session.ws_connect = AsyncMock(return_value=ws)
    if owner == "connector":
        client = HAWebSocketClient(
            ha_base_url="http://ha.test", ha_access_token="synthetic-token", dispatch=AsyncMock()
        )
        client._ws_session = session
        invoke = client._connect
        cleanup = client._close_connection
    elif owner == "module":
        client = HomeAssistantModule()
        client._url, client._token = "http://ha.test", "synthetic-token"
        client._config, client._ws_session = HomeAssistantConfig(), session
        invoke = client._ws_connect
        cleanup = client._ws_close
    else:
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=ws)
        context.__aexit__ = AsyncMock(return_value=False)
        session.ws_connect = MagicMock(return_value=context)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(ha.aiohttp, "ClientSession", lambda **kw: session)
        client = ha.HAStatisticsClient(ha_url="http://ha.test", ha_token="synthetic-token")

        async def invoke():
            return await client.get_statistics(
                statistic_ids=["sensor.synthetic"], start="s", end="e"
            )

        cleanup = AsyncMock()
    try:
        if outcome == "ok":
            await invoke()
        elif outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await invoke()
        else:
            expected = (
                ha.HAStatisticsError if owner == "statistics" else (RuntimeError, TimeoutError)
            )
            with pytest.raises(expected) as caught:
                await invoke()
            if owner == "statistics" or outcome != "timeout":
                assert "SYNTHETIC-PRIVATE-PROVIDER" not in str(caught.value)
        assert ws.receive_json.await_args_list[0].kwargs == {"timeout": 10.0}
        if outcome != "greeting":
            assert ws.receive_json.await_args_list[1].kwargs == {"timeout": 10.0}
            assert ws.send_json.await_args_list[0].args[0] == {
                "type": "auth",
                "access_token": "synthetic-token",
            }
        else:
            ws.send_json.assert_not_awaited()
        if owner == "statistics":
            context.__aexit__.assert_awaited_once()
            session.__aexit__.assert_awaited_once()
        elif outcome != "ok":
            ws.close.assert_awaited_once()
        else:
            assert ws.send_json.await_args_list[1].args[0]["features"] == {"coalesce_messages": 1}
            ws.close.assert_not_awaited()
    finally:
        await cleanup()


@pytest.mark.parametrize(
    ("token", "verify", "phase_timeout"),
    [
        ("synthetic-token", True, 5.0),
        (None, False, httpx.Timeout(30, connect=10)),
    ],
)
async def test_rest_factory_headers_tls_timeout_status_and_no_retry(
    monkeypatch, token, verify, phase_timeout
):
    constructor = httpx.AsyncClient
    captured = []
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(403, json={"not": "authorized"})

    def construct(**kwargs):
        captured.append(dict(kwargs))
        return constructor(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ha.httpx, "AsyncClient", construct)
    client = ha.rest_client("https://ha.test", token, verify_ssl=verify, timeout=phase_timeout)
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await ha.get_json(client, "/api/history", params={"entity_id": "sensor.synthetic"})
    assert client.is_closed
    assert captured[0]["verify"] is verify and captured[0]["timeout"] == phase_timeout
    assert len(requests) == 1 and requests[0].url.params["entity_id"] == "sensor.synthetic"
    assert requests[0].headers["Content-Type"] == "application/json"
    if token:
        assert requests[0].headers["Authorization"] == "Bearer " + token
    else:
        assert "Authorization" not in requests[0].headers


@pytest.mark.parametrize("consumer", ["poller", "history"])
@pytest.mark.parametrize("outcome", ["ok", "status", "transport", "cancel", "deadline", "redirect"])
async def test_rest_connector_owners_close_and_keep_total_budget(monkeypatch, consumer, outcome):
    made, settings, calls, budgets = [], [], [], []
    real_timeout = asyncio.timeout

    def budget(seconds):
        budgets.append(seconds)
        return real_timeout(0.01 if outcome == "deadline" else seconds)

    monkeypatch.setattr(rest.asyncio, "timeout", budget)
    error = httpx.ReadTimeout("synthetic-error")

    async def handler(request):
        calls.append(request)
        if outcome == "transport":
            raise error
        if outcome == "cancel":
            raise asyncio.CancelledError()
        if outcome == "deadline":
            await asyncio.Future()
        if outcome == "redirect" and request.url.path != "/api/redirected":
            return httpx.Response(302, headers={"Location": "/api/redirected"})
        return httpx.Response(
            401 if outcome == "status" else 200,
            json=[] if consumer == "poller" else [[{"state": "1"}]],
        )

    def factory(base, token, **kwargs):
        settings.append((base, token, kwargs))
        client = httpx.AsyncClient(
            base_url=base,
            transport=httpx.MockTransport(handler),
            follow_redirects=kwargs["follow_redirects"],
        )
        made.append(client)
        return client

    monkeypatch.setattr(rest, "rest_client", factory)
    if consumer == "poller":
        target = rest.HARestPoller(
            base_url="http://ha.test",
            access_token="synthetic-token",
            state_cache=rest.HAStateCache(),
        )
        invoke = target.poll_once
    else:
        target = rest.HAMeasurementHistoryRecovery(
            base_url="http://ha.test",
            access_token="synthetic-token",
            endpoint_identity="synthetic",
            db_pool=object(),
            on_measurement=AsyncMock(),
        )

        async def invoke():
            return await target._get_json(
                "http://ha.test/api/history", params={"filter_entity_id": "sensor.synthetic"}
            )

    if outcome in {"ok", "redirect"}:
        await invoke()
    else:
        cls = {
            "status": httpx.HTTPStatusError,
            "transport": httpx.ReadTimeout,
            "cancel": asyncio.CancelledError,
            "deadline": TimeoutError,
        }[outcome]
        with pytest.raises(cls) as caught:
            await invoke()
        if outcome == "transport":
            assert caught.value is error
    assert budgets == [30.0]
    assert settings[0] == (
        "http://ha.test",
        "synthetic-token",
        {"timeout": 30.0, "trust_env": False, "follow_redirects": True},
    )
    assert len(calls) == (2 if outcome == "redirect" else 1) and made[0].is_closed
    if consumer == "history":
        assert calls[0].url.params["filter_entity_id"] == "sensor.synthetic"
