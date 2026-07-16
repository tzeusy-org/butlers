"""Real-Postgres Telegram tap -> approval transition -> audit regression."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.middleware import ApiKeyMiddleware
from butlers.api.routers import approvals as approvals_router
from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import (
    APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER,
    mint_approval_callback_token,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_ACTION_ID = UUID("12345678-1234-5678-1234-567812345678")
_REQUESTED_AT = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
_SECRET = "test-only-approval-callback-secret"
_DASHBOARD_API_KEY = "test-only-dashboard-api-key"
_CALLBACK_CONNECTOR_TOKEN = "test-only-callback-connector-token"


class _DbManager:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.butler_names = ["relationship"]

    def pool(self, butler_name: str) -> Any:
        assert butler_name == "relationship"
        return self._pool


class _CallbackHttpClient:
    """Route dashboard requests through ASGI and emulate the Telegram API."""

    def __init__(self, app: FastAPI) -> None:
        self._dashboard = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://dashboard-api:41200",
        )
        self.telegram_calls: list[tuple[str, dict[str, Any]]] = []
        self.dashboard_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        assert url.startswith("http://dashboard-api:41200/")
        self.dashboard_calls.append(("GET", url, kwargs))
        return await self._dashboard.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url.startswith("http://dashboard-api:41200/"):
            self.dashboard_calls.append(("POST", url, kwargs))
            return await self._dashboard.post(url, **kwargs)
        self.telegram_calls.append((url, kwargs.get("json", {})))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"ok": True},
        )

    async def close(self) -> None:
        await self._dashboard.aclose()


def _protected_approvals_app(pool: Any) -> FastAPI:
    """Build the real API-key gate with only the callback service credential."""
    app = FastAPI()
    app.state.approval_callback_connector_token = _CALLBACK_CONNECTOR_TOKEN
    app.add_middleware(ApiKeyMiddleware, api_key=_DASHBOARD_API_KEY)
    app.include_router(approvals_router.router)
    app.dependency_overrides[approvals_router._get_db_manager] = lambda: _DbManager(pool)
    app.dependency_overrides[approvals_router.get_mcp_manager] = lambda: MagicMock()
    return app


@pytest.fixture
async def approval_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
            CREATE TABLE pending_actions (
                id UUID PRIMARY KEY,
                tool_name TEXT NOT NULL,
                tool_args JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_summary TEXT,
                session_id UUID,
                requested_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ,
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                execution_result JSONB,
                why TEXT,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                approval_rule_id UUID
            )
        """)
        await pool.execute("""
            CREATE TABLE approval_events (
                event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id UUID NOT NULL REFERENCES pending_actions(id),
                rule_id UUID,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                occurred_at TIMESTAMPTZ NOT NULL
            )
        """)
        await pool.execute("""
            CREATE TABLE public.audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                note TEXT,
                ip INET,
                request_id UUID,
                ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                metadata JSONB,
                result TEXT,
                error TEXT
            )
        """)
        await pool.execute(
            """
            INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)
            VALUES ($1, 'send_telegram', '{"chat_id":"42"}'::jsonb, 'pending', $2)
            """,
            _ACTION_ID,
            _REQUESTED_AT,
        )
        yield pool


async def test_owner_reject_tap_transitions_and_audits_via_standard_approval_route(
    approval_pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _protected_approvals_app(approval_pool)

    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url="http://dashboard-api:41200",
            approval_callback_secret=_SECRET,
            approval_callback_connector_token=_CALLBACK_CONNECTOR_TOKEN,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    http_client = _CallbackHttpClient(app)
    connector._http_client = http_client
    owner_resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(telegram_bot_module, "resolve_owner_channel_via_definer", owner_resolver)

    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="r",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )
    try:
        handled = await connector._maybe_handle_approval_callback(
            {
                "callback_query": {
                    "id": "cbq1",
                    "data": token,
                    "from": {"id": 9001},
                    "message": {"chat": {"id": 9001}, "message_id": 44},
                }
            }
        )
    finally:
        await http_client.close()

    assert handled is True
    owner_resolver.assert_awaited_once_with(connector._db_pool, "telegram_bot", "9001")
    action = await approval_pool.fetchrow(
        "SELECT status, decided_by FROM pending_actions WHERE id = $1", _ACTION_ID
    )
    assert dict(action) == {"status": "rejected", "decided_by": "human:owner@telegram"}
    event_actor = await approval_pool.fetchval(
        "SELECT actor FROM approval_events WHERE action_id = $1", _ACTION_ID
    )
    assert event_actor == "human:owner@telegram"
    audit = await approval_pool.fetchrow(
        "SELECT actor, action, target FROM public.audit_log WHERE target = $1", str(_ACTION_ID)
    )
    assert dict(audit) == {
        "actor": "human:owner@telegram",
        "action": "approval.deny",
        "target": str(_ACTION_ID),
    }
    assert [url.rsplit("/", 1)[-1] for url, _ in http_client.telegram_calls] == [
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
    ]
    assert [method for method, _, _ in http_client.dashboard_calls] == ["GET", "POST"]
    for _, _, call_kwargs in http_client.dashboard_calls:
        assert call_kwargs["headers"][APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER] == (
            _CALLBACK_CONNECTOR_TOKEN
        )


@pytest.mark.parametrize(
    "callback_headers",
    [
        {},
        {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: "wrong-callback-connector-token"},
    ],
    ids=["missing-credential", "wrong-credential"],
)
async def test_protected_callback_api_rejects_missing_or_wrong_connector_credential(
    approval_pool,
    callback_headers: dict[str, str],
) -> None:
    app = _protected_approvals_app(approval_pool)
    decision_headers = {
        **callback_headers,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dashboard-api:41200"
    ) as client:
        detail = await client.get(f"/api/approvals/{_ACTION_ID}", headers=callback_headers)
        decision = await client.post(
            f"/api/approvals/{_ACTION_ID}/deny", json={}, headers=decision_headers
        )
        unrelated = await client.get("/api/approvals", headers=callback_headers)

    assert detail.status_code == 401
    assert decision.status_code == 401
    assert unrelated.status_code == 401
    assert (
        await approval_pool.fetchval("SELECT status FROM pending_actions WHERE id = $1", _ACTION_ID)
        == "pending"
    )


async def test_callback_credential_cannot_be_used_as_a_dashboard_edit_credential(
    approval_pool,
) -> None:
    app = _protected_approvals_app(approval_pool)
    callback_headers = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CALLBACK_CONNECTOR_TOKEN}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://dashboard-api:41200"
    ) as client:
        missing_provenance = await client.post(
            f"/api/approvals/{_ACTION_ID}/deny", json={}, headers=callback_headers
        )
        attempted_edit = await client.post(
            f"/api/approvals/{_ACTION_ID}/approve",
            json={"edits": {"chat_id": "attacker"}},
            headers={
                **callback_headers,
                "X-Butlers-Decision-Actor": "owner@telegram",
            },
        )

    assert missing_provenance.status_code == 403
    assert attempted_edit.status_code == 403
    assert (
        await approval_pool.fetchval("SELECT status FROM pending_actions WHERE id = $1", _ACTION_ID)
        == "pending"
    )


@pytest.mark.parametrize("connector_token", [None, "wrong-callback-connector-token"])
async def test_callback_without_a_valid_connector_credential_never_transitions_or_claims_success(
    approval_pool,
    monkeypatch: pytest.MonkeyPatch,
    connector_token: str | None,
) -> None:
    app = _protected_approvals_app(approval_pool)
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url="http://dashboard-api:41200",
            approval_callback_secret=_SECRET,
            approval_callback_connector_token=connector_token,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    http_client = _CallbackHttpClient(app)
    connector._http_client = http_client
    monkeypatch.setattr(
        telegram_bot_module,
        "resolve_owner_channel_via_definer",
        AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True)),
    )
    token = mint_approval_callback_token(
        action_id=_ACTION_ID,
        verb="r",
        requested_at=_REQUESTED_AT,
        secret=_SECRET,
    )

    try:
        handled = await connector._maybe_handle_approval_callback(
            {
                "callback_query": {
                    "id": "cbq1",
                    "data": token,
                    "from": {"id": 9001},
                    "message": {"chat": {"id": 9001}, "message_id": 44},
                }
            }
        )
    finally:
        await http_client.close()

    assert handled is True
    status = await approval_pool.fetchval(
        "SELECT status FROM pending_actions WHERE id = $1", _ACTION_ID
    )
    assert status == "pending"
    assert [url.rsplit("/", 1)[-1] for url, _ in http_client.telegram_calls] == [
        "answerCallbackQuery"
    ]
    assert http_client.telegram_calls[0][1]["text"] == ""
