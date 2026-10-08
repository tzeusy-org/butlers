"""Real-Postgres Telegram ``dsk1`` tap -> decision intent, through the real auth gate.

bu-ckkpz.3, REQ-owner-decision-desk-004. The connector verifies the owner
channel and the HMAC against the prompt row the dashboard API returns, then
records the intent through the connector-scoped ``choose`` route. The owner's
dashboard intent route and the connector's prompt routes are not
interchangeable.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers import decisions as decisions_router
from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER
from butlers.core.decision_callbacks import mint_decision_callback_token
from tests.api.auth_helpers import _DomainOwnerState
from tests.decision_desk_helpers import (
    make_bead,
    make_digest,
    migrated_switchboard_db,
    switchboard_pool,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_CREATED_AT = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)
_SECRET = "test-only-decision-callback-secret"
_DASHBOARD_API_KEY = "test-only-dashboard-api-key"
_CONNECTOR_TOKEN = "test-only-callback-connector-token"
_API = "http://dashboard-api:41200"


class _DbManager:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def pool(self, butler_name: str) -> Any:
        assert butler_name == "switchboard"
        return self._pool


class _CallbackHttpClient:
    """Route dashboard requests through ASGI and record Telegram API calls."""

    def __init__(self, app: FastAPI) -> None:
        self._dashboard = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_API)
        self.telegram_calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        assert url.startswith(f"{_API}/")
        return await self._dashboard.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url.startswith(f"{_API}/"):
            return await self._dashboard.post(url, **kwargs)
        self.telegram_calls.append((url.rsplit("/", 1)[-1], kwargs.get("json", {})))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    async def close(self) -> None:
        await self._dashboard.aclose()


def _app(pool: Any) -> FastAPI:
    app = FastAPI()
    app.state.approval_callback_connector_token = _CONNECTOR_TOKEN
    app.state.owner_auth_service = _DomainOwnerState(_DASHBOARD_API_KEY)
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env(_DASHBOARD_API_KEY))
    app.include_router(decisions_router.router)
    app.dependency_overrides[decisions_router._get_db_manager] = lambda: _DbManager(pool)
    return app


@pytest.fixture(scope="module")
def desk_db_url(postgres_container) -> str:
    return migrated_switchboard_db(postgres_container)


@pytest.fixture
async def pool(desk_db_url):
    pool = await switchboard_pool(desk_db_url)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def digest():
    with patch.object(
        decisions_router, "compute_decision_digest", return_value=make_digest(make_bead())
    ) as mocked:
        yield mocked


@pytest.fixture
def owner(monkeypatch):
    resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(telegram_bot_module, "resolve_owner_channel_via_definer", resolver)
    return resolver


async def _prompt(pool: asyncpg.Pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO switchboard.decision_prompts "
        "(bead_id, options, default_option, created_at, delivery_outcome) "
        "VALUES ('bu-test1', $1::jsonb, 'Hold', $2, 'delivered') RETURNING id",
        json.dumps(["Ship it", "Hold"]),
        _CREATED_AT,
    )


def _connector(pool: Any) -> tuple[TelegramBotConnector, _CallbackHttpClient]:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=_API,
            approval_callback_secret=_SECRET,
            approval_callback_connector_token=_CONNECTOR_TOKEN,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    http_client = _CallbackHttpClient(_app(pool))
    connector._http_client = http_client
    return connector, http_client


def _tap(token: str, query_id: str = "cbq-1") -> dict[str, Any]:
    return {
        "callback_query": {
            "id": query_id,
            "data": token,
            "from": {"id": 9001},
            "message": {"chat": {"id": 9001}, "message_id": 44},
        }
    }


def _token(prompt_id: UUID, index: int, created_at: datetime = _CREATED_AT) -> str:
    return mint_decision_callback_token(
        prompt_id=prompt_id, option_index=index, created_at=created_at, secret=_SECRET
    )


async def _intents(pool) -> list[tuple[str, str, str, str]]:
    rows = await pool.fetch(
        "SELECT option, source, actor, status FROM switchboard.decision_intents"
    )
    return [(r["option"], r["source"], r["actor"], r["status"]) for r in rows]


async def test_owner_tap_records_a_pending_intent_and_clears_the_keyboard(
    pool, digest, owner
) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        handled = await connector._process_update(_tap(_token(prompt_id, 1)))
        repeat = await connector._maybe_handle_decision_callback(
            _tap(_token(prompt_id, 1), "cbq-2")
        )
    finally:
        await http_client.close()

    assert handled is None and repeat is True
    assert await _intents(pool) == [("Hold", "telegram", "owner@telegram", "pending")]
    calls = http_client.telegram_calls
    assert [name for name, _ in calls] == [
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
    ]
    assert calls[0][1]["text"] == "Choice recorded."
    assert calls[3][1]["text"] == "Already handled."
    assert calls[1][1]["text"] == "Decision recorded: Hold\nPending update to the tracker."
    assert calls[2][1]["reply_markup"] is None


async def test_conflicting_second_tap_is_refused(pool, digest, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 1), "cbq-2"))
    finally:
        await http_client.close()

    assert await _intents(pool) == [("Ship it", "telegram", "owner@telegram", "pending")]
    assert http_client.telegram_calls[3] == (
        "answerCallbackQuery",
        {
            "callback_query_id": "cbq-2",
            "text": "Already handled.",
        },
    )


@pytest.mark.parametrize("case", ["forged", "wrong_binding", "non_owner", "non_primary"])
async def test_unverified_taps_record_nothing(pool, digest, owner, case) -> None:
    prompt_id = await _prompt(pool)
    token = _token(prompt_id, 0)
    if case == "forged":
        token = token.rsplit(":", 1)[0] + ":" + "0" * 16
    elif case == "wrong_binding":
        token = _token(prompt_id, 0, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    elif case == "non_owner":
        owner.return_value = None
    else:
        owner.return_value = (SimpleNamespace(roles=["owner"]), False)
    connector, http_client = _connector(pool)
    try:
        handled = await connector._maybe_handle_decision_callback(_tap(token))
    finally:
        await http_client.close()

    assert handled is True
    assert await _intents(pool) == []
    assert http_client.telegram_calls == [
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": ""})
    ]


async def test_closed_decision_tap_says_so(pool, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        with patch.object(decisions_router, "compute_decision_digest", return_value=make_digest()):
            await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert await _intents(pool) == []
    assert http_client.telegram_calls[0][1]["text"] == "Already handled."
    assert [name for name, _ in http_client.telegram_calls] == [
        "answerCallbackQuery",
        "editMessageReplyMarkup",
    ]


async def test_changed_options_tap_points_at_the_dashboard(pool, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    reordered = make_digest(make_bead(options=("Hold", "Ship it")))
    try:
        with patch.object(decisions_router, "compute_decision_digest", return_value=reordered):
            await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert await _intents(pool) == []
    assert http_client.telegram_calls[0][1]["text"] == "Options changed; open the dashboard."


async def test_unreachable_api_keeps_the_keyboard(pool, digest, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    connector._submit_decision_choice = AsyncMock(return_value=(None, None))
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert [name for name, _ in http_client.telegram_calls] == ["answerCallbackQuery"]
    assert http_client.telegram_calls[0][1]["text"].startswith("Couldn't record that")


@pytest.mark.parametrize("failure", ["network", "server_error"])
async def test_unreachable_prompt_lookup_says_retry(pool, digest, owner, failure) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    real_get = http_client.get

    async def failing_get(url: str, **kwargs: Any) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("dashboard-api down")
        return httpx.Response(503, request=httpx.Request("GET", url))

    http_client.get = failing_get  # type: ignore[method-assign]
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        http_client.get = real_get  # type: ignore[method-assign]
        await http_client.close()

    assert await _intents(pool) == []
    assert [name for name, _ in http_client.telegram_calls] == ["answerCallbackQuery"]
    assert http_client.telegram_calls[0][1]["text"].startswith("Couldn't record that")


async def test_unknown_prompt_gets_only_the_generic_acknowledgement(pool, digest, owner) -> None:
    connector, http_client = _connector(pool)
    stranger = UUID("99999999-9999-4999-8999-999999999999")
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(stranger, 0)))
    finally:
        await http_client.close()

    assert http_client.telegram_calls == [
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": ""})
    ]


async def test_credentials_are_not_interchangeable(pool, digest) -> None:
    prompt_id = await _prompt(pool)
    connector_headers = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN}
    owner_headers = {"X-API-Key": _DASHBOARD_API_KEY}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url=_API
    ) as client:
        connector_on_owner_route = await client.post(
            "/api/decisions/bu-test1/intent",
            json={"option": "Hold"},
            headers={**connector_headers, "X-Butlers-Decision-Actor": "owner@telegram"},
        )
        owner_on_choose = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0},
            headers={**owner_headers, "X-Butlers-Decision-Actor": "owner@telegram"},
        )
        connector_without_actor = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0},
            headers=connector_headers,
        )
        connector_on_digest = await client.get("/api/decisions", headers=connector_headers)
        owner_records = await client.post(
            "/api/decisions/bu-test1/intent", json={"option": "Hold"}, headers=owner_headers
        )

    assert connector_on_owner_route.status_code == 401
    assert owner_on_choose.status_code == 403
    assert connector_without_actor.status_code == 403
    assert connector_on_digest.status_code == 401
    assert owner_records.status_code == 200
    assert owner_records.json()["data"]["status"] == "pending"
    assert owner_records.json()["meta"]["created"] is True
    assert await _intents(pool) == [("Hold", "dashboard", "owner@dashboard", "pending")]
