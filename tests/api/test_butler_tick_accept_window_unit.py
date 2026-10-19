"""A forced tick that outlasts the accept window answers 202 "running" (bu-o5jqmh).

The ``tick`` tool runs due schedules to completion, which for a prompt-mode schedule is a whole
LLM session, while the route used to give up after 5 s with 503 ``butler_timeout`` and the
dashboard toasted a started tick as failed. The route now waits only ``_TICK_ACCEPT_WINDOW_S``;
past it, it answers 202 ``running`` and the tick finishes in the background, which audits the
outcome exactly once. Replies inside the window keep their mapping (200 ran/busy, 503).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from httpx import ASGITransport, AsyncClient

from butlers.api import accept_window
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.butlers import _get_db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_RAN = {"dispatched": 2}
_BUSY = {"status": "busy", "dispatched": 0, "message": "scheduled work is already running"}


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


def _reply(payload: dict):
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])


async def _drain() -> None:
    await asyncio.gather(*accept_window.TASKS, return_exceptions=True)


async def _post_tick(
    app, call_tool, *, get_client=None, window=0.05, release=None, raise_app=True, **patches
):
    client = MagicMock()
    client.call_tool = call_tool
    manager = MagicMock()
    manager.get_client = get_client or AsyncMock(return_value=client)
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    app.dependency_overrides[get_butler_configs] = lambda: [
        ButlerConnectionInfo(name="qa", port=8000)
    ]
    app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
    audit = AsyncMock()
    with (
        patch("butlers.api.routers.butlers.log_audit_entry", audit),
        patch.multiple("butlers.api.routers.butlers", _TICK_ACCEPT_WINDOW_S=window, **patches),
    ):
        transport = ASGITransport(app=app, raise_app_exceptions=raise_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.post("/api/butlers/qa/tick", headers=_HEADERS)
        audit.awaits_at_response = audit.await_count
        if release is not None:
            # Let the background tick finish while the audit patch is still active.
            release.set()
            await _drain()
    return resp, audit


def _slow(release: asyncio.Event, payload: dict):
    async def call_tool(*_args, **_kwargs):
        await release.wait()
        return _reply(payload)

    return call_tool


async def test_a_tick_past_the_window_answers_202_running_not_a_failure(app) -> None:
    release = asyncio.Event()

    resp, audit = await _post_tick(app, _slow(release, _RAN), release=release)

    assert resp.status_code == 202
    data = resp.json()["data"]
    assert data["running"] is True
    assert data["success"] is True
    assert audit.awaits_at_response == 0


async def test_the_background_finish_audits_the_outcome_exactly_once(app) -> None:
    release = asyncio.Event()

    _resp, audit = await _post_tick(app, _slow(release, _RAN), release=release)

    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[1:3] == ("qa", "tick")
    assert args[3]["tick_outcome"] == "ran"
    assert kwargs.get("result", "success") == "success"


async def test_a_failure_after_the_window_is_audited_as_an_error_and_not_raised(app) -> None:
    release = asyncio.Event()

    async def slow_unreachable(*_args, **_kwargs):
        await release.wait()
        raise ButlerUnreachableError("qa")

    resp, audit = await _post_tick(app, slow_unreachable, release=release)

    assert resp.status_code == 202
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"
    assert not accept_window.TASKS


@pytest.mark.parametrize(
    ("payload", "success", "outcome"), [(_RAN, True, "ran"), (_BUSY, False, "busy")]
)
async def test_a_reply_inside_the_window_keeps_its_200_mapping(
    app, payload, success, outcome
) -> None:
    resp, audit = await _post_tick(app, AsyncMock(return_value=_reply(payload)), window=5.0)

    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is success
    assert resp.json()["data"]["running"] is False
    assert audit.await_args.args[3]["tick_outcome"] == outcome


async def test_an_unreachable_butler_inside_the_window_is_still_a_503(app) -> None:
    call_tool = AsyncMock(side_effect=ButlerUnreachableError("qa"))

    resp, audit = await _post_tick(app, call_tool, window=5.0)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "butler_unreachable"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"


async def test_a_butler_that_will_not_connect_is_still_a_503_timeout(app) -> None:
    async def never_connects(_name):
        await asyncio.sleep(3600)

    resp, audit = await _post_tick(
        app,
        AsyncMock(),
        get_client=never_connects,
        window=5.0,
        _STATUS_TIMEOUT_S=0.01,
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "butler_timeout"
    audit.assert_awaited_once()


async def test_the_tick_call_is_not_cut_off_at_the_old_status_timeout(app) -> None:
    async def slow_but_ok(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        return _reply(_RAN)

    resp, audit = await _post_tick(
        app, slow_but_ok, release=asyncio.Event(), _STATUS_TIMEOUT_S=0.02
    )

    assert resp.status_code == 202
    audit.assert_awaited_once()
    assert audit.await_args.args[3]["tick_outcome"] == "ran"
    assert audit.await_args.kwargs.get("result", "success") == "success"


def test_the_accept_window_stays_below_the_browsers_request_budget() -> None:
    from butlers.api.routers import butlers as butlers_module

    # frontend/src/api/client.ts API_REQUEST_TIMEOUT_MS is 15 s; leave headroom for the network
    # and any proxy, or a tick that did start reads as a failed one again.
    assert 0 < butlers_module._TICK_ACCEPT_WINDOW_S <= 12


async def test_a_tick_cancelled_after_the_202_is_logged_and_never_audited(app, caplog) -> None:
    release = asyncio.Event()

    with caplog.at_level("INFO", logger="butlers.api.routers.butlers"):
        resp, audit = await _post_tick(app, _slow(release, _RAN))
        for task in list(accept_window.TASKS):
            task.cancel()
        await _drain()

    assert resp.status_code == 202
    audit.assert_not_awaited()
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "cancelled" in warnings[0].getMessage()
    assert "not audited" in warnings[0].getMessage()
    assert "forced-tick:qa" in warnings[0].getMessage()
    assert warnings[0].name == "butlers.api.routers.butlers"
    assert not accept_window.TASKS
    assert not accept_window._DETACHED


async def test_a_non_http_failure_after_the_202_is_logged_as_an_error_with_a_traceback(
    app, caplog
) -> None:
    release = asyncio.Event()

    async def slow_bug(*_args, **_kwargs):
        await release.wait()
        raise RuntimeError("parse path bug")

    with caplog.at_level("INFO", logger="butlers.api.routers.butlers"):
        resp, _audit = await _post_tick(app, slow_bug, release=release)

    assert resp.status_code == 202
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].name == "butlers.api.routers.butlers"
    assert "parse path bug" in repr(errors[0].exc_info[1])


@pytest.mark.parametrize(
    "exc",
    [ToolError("provider said something private"), ConnectionError("stream closed")],
    ids=["tool-error", "connection-error"],
)
async def test_a_failure_after_the_202_is_audited_once_by_type_only(app, exc) -> None:
    release = asyncio.Event()

    async def slow_failure(*_args, **_kwargs):
        await release.wait()
        raise exc

    resp, audit = await _post_tick(app, slow_failure, release=release)

    assert resp.status_code == 202
    audit.assert_awaited_once()
    assert audit.await_args.args[1:3] == ("qa", "tick")
    assert audit.await_args.kwargs["result"] == "error"
    assert audit.await_args.kwargs["error"] == f"Tick failed: {type(exc).__name__}"
    assert str(exc) not in audit.await_args.kwargs["error"]


async def test_a_tool_error_inside_the_window_is_audited_and_stays_a_500(app) -> None:
    call_tool = AsyncMock(side_effect=ToolError("provider said something private"))

    resp, audit = await _post_tick(app, call_tool, window=5.0, raise_app=False)

    assert resp.status_code == 500
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"
    assert audit.await_args.kwargs["error"] == "Tick failed: ToolError"


async def test_the_tick_call_is_not_wrapped_in_a_client_side_timeout(app) -> None:
    wrapped: list[str] = []
    real_wait_for = asyncio.wait_for

    def spy(aw, *args, **kwargs):
        wrapped.append(getattr(getattr(aw, "cr_code", None), "co_name", repr(aw)))
        return real_wait_for(aw, *args, **kwargs)

    async def tick_tool(*_args, **_kwargs):
        return _reply(_RAN)

    with patch("butlers.api.routers.butlers.asyncio.wait_for", new=spy):
        resp, _audit = await _post_tick(app, tick_tool, window=5.0)

    assert resp.status_code == 200
    # The bounded get_client wait is expected; the tick tool call must never be bounded.
    assert wrapped, "the get_client wait should still be bounded"
    assert "tick_tool" not in wrapped
