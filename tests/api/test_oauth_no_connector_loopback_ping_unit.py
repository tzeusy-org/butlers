"""Completing Google OAuth makes no HTTP call to a connector's loopback listener.

The API process and the Gmail connector run in different containers (Compose) or
pods (k3s), and the connector's health listener is bound to ``127.0.0.1`` by design.
A ``POST http://127.0.0.1:<port>/reload`` from the API can therefore never reach it
there, and the failure used to be logged at debug level only. The ping was removed
(bu-c4fweu); the connector picks up a new token at its next periodic account rescan.

Both Google callbacks are driven: ``oauth_google_callback`` over HTTP and
``_google_callback_from_state`` directly (the generalised provider route delegates
to it). The oauth module's ``httpx`` is swapped for a recorder, so any client the
callback opens is observed rather than silently swallowed by its own ``except``.
A positive control proves the recorder sees a call when one is made.
"""

from __future__ import annotations

import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _clear_state_store,
    _generate_state,
    _google_callback_from_state,
    _StateEntry,
    _store_state,
)
from butlers.google_account_registry import GoogleAccountNotFoundError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PAYLOAD: dict[str, Any] = {
    "access_token": "synthetic-access-token-not-real",
    "refresh_token": "synthetic-refresh-token-not-real",
    "scope": "scope-a scope-b",
    "token_type": "Bearer",
    "expires_in": 3600,
}
_USERINFO = {"email": "no-ping@example.invalid", "name": "No Ping"}


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


class _RecordingHttpx:
    """Stands in for ``oauth.httpx``: records every request, answers none."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        outer = self

        class AsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> AsyncClient:
                return self

            async def __aexit__(self, *exc_info: Any) -> bool:
                return False

            async def post(self, url: str, **kwargs: Any) -> Any:
                outer.requests.append(("POST", url))
                raise httpx.ConnectError("recorded, not sent")

            async def get(self, url: str, **kwargs: Any) -> Any:
                outer.requests.append(("GET", url))
                raise httpx.ConnectError("recorded, not sent")

        self.AsyncClient = AsyncClient

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)


def _patches(recorder: _RecordingHttpx) -> list[Any]:
    @asynccontextmanager
    async def _acquire():
        yield MagicMock()

    pool = MagicMock()
    pool.acquire = _acquire
    cred_store = AsyncMock()
    cred_store.store = AsyncMock()
    return [
        patch.object(oauth_module, "httpx", recorder),
        patch(
            "butlers.api.routers.oauth._resolve_app_credentials",
            AsyncMock(return_value=("client-id", "client-secret")),
        ),
        patch(
            "butlers.api.routers.oauth._exchange_code_for_tokens",
            AsyncMock(return_value=dict(_PAYLOAD)),
        ),
        patch(
            "butlers.api.routers.oauth._fetch_google_userinfo",
            AsyncMock(return_value=dict(_USERINFO)),
        ),
        patch(
            "butlers.api.routers.oauth.get_google_account",
            AsyncMock(side_effect=GoogleAccountNotFoundError("absent")),
        ),
        patch(
            "butlers.api.routers.oauth.create_google_account",
            AsyncMock(return_value=MagicMock(entity_id=uuid.uuid4())),
        ),
        patch("butlers.api.routers.oauth.store_app_credentials", AsyncMock()),
        patch("butlers.api.routers.oauth.store_google_credentials", AsyncMock()),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=cred_store),
        patch("butlers.api.routers.oauth._get_shared_pool", return_value=pool),
        patch("butlers.api.routers.oauth._emit_oauth_audit", AsyncMock()),
    ]


async def _complete_via_route(recorder: _RecordingHttpx) -> int:
    app = create_app()
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: MagicMock()
    state = _generate_state()
    _store_state(state)
    with ExitStack() as stack:
        for patcher in _patches(recorder):
            stack.enter_context(patcher)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            response = await client.get(
                "/api/oauth/google/callback", params={"code": "4/synthetic", "state": state}
            )
    return response.status_code


async def _complete_via_state(recorder: _RecordingHttpx) -> int:
    state_entry = _StateEntry(expiry=time.monotonic() + 300, provider="google")
    with ExitStack() as stack:
        for patcher in _patches(recorder):
            stack.enter_context(patcher)
        response = await _google_callback_from_state(
            code="4/synthetic",
            state_entry=state_entry,
            db_manager=MagicMock(),
            page_of_origin=None,
        )
    return response.status_code


_DRIVERS = {
    "oauth_google_callback": _complete_via_route,
    "_google_callback_from_state": _complete_via_state,
}


@pytest.mark.parametrize("site", list(_DRIVERS))
async def test_completion_makes_no_http_call_to_a_connector(site: str) -> None:
    recorder = _RecordingHttpx()

    status = await _DRIVERS[site](recorder)

    assert status in (302, 307), "the callback must still complete and redirect"
    assert recorder.requests == [], (
        f"the {site} callback issued HTTP requests that cannot reach a connector in another "
        f"container: {recorder.requests!r}"
    )


async def test_the_recorder_sees_a_call_when_one_is_made() -> None:
    """Positive control: the empty-list assertion above can fail."""
    recorder = _RecordingHttpx()

    with patch.object(oauth_module, "httpx", recorder):
        with pytest.raises(httpx.ConnectError):
            async with oauth_module.httpx.AsyncClient(timeout=2.0) as client:
                await client.post("http://127.0.0.1:40082/reload")

    assert recorder.requests == [("POST", "http://127.0.0.1:40082/reload")]
