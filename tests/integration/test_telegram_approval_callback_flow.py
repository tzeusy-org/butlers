"""Real-Postgres Telegram tap -> approval transition -> audit regression."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.middleware import ApiKeyMiddleware
from butlers.api.routers import approvals as approvals_router
from butlers.config import ApprovalRiskTier
from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import (
    APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER,
    mint_approval_callback_token,
)
from butlers.db import register_jsonb_codec
from butlers.modules.approvals import gate as approvals_gate_module
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.modules.approvals.module import ApprovalsModule
from butlers.testing.migration import create_migrated_test_db, migration_db_name

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


class _ExecutingButlerClient:
    """Expose the real owning-butler executor through the MCP dispatch seam."""

    def __init__(self, module: ApprovalsModule) -> None:
        self._module = module
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        assert tool_name == "dispatch_approved_action"
        result = await self._module._dispatch_approved_action_by_id(arguments["action_id"])
        return SimpleNamespace(
            is_error=False,
            content=[SimpleNamespace(text=json.dumps(result, default=str))],
        )


class _ExecutingMcpManager:
    """Minimal manager that keeps the API's real MCP dispatch path intact."""

    butler_names = ["relationship"]

    def __init__(self, client: _ExecutingButlerClient) -> None:
        self._client = client

    async def get_client(self, butler_name: str) -> _ExecutingButlerClient:
        assert butler_name == "relationship"
        return self._client


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


def _protected_approvals_app(pool: Any, *, mcp_mgr: Any | None = None) -> FastAPI:
    """Build the real API-key gate with only the callback service credential."""
    app = FastAPI()
    app.state.approval_callback_connector_token = _CALLBACK_CONNECTOR_TOKEN
    app.add_middleware(ApiKeyMiddleware, api_key=_DASHBOARD_API_KEY)
    app.include_router(approvals_router.router)
    app.dependency_overrides[approvals_router._get_db_manager] = lambda: _DbManager(pool)
    app.dependency_overrides[approvals_router.get_mcp_manager] = lambda: (
        mcp_mgr if mcp_mgr is not None else MagicMock()
    )
    return app


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Use the production core + approvals migrations, not hand-rolled DDL."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def approval_pool(migrated_db_url: str):
    """Return an isolated JSONB-aware pool over the migrated approval schema."""
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE TABLE public.audit_log, autonomy_suggestions, autonomy_approval_history, "
        "approval_events, pending_actions, approval_rules CASCADE"
    )
    await pool.execute(
        """
        INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at)
        VALUES ($1, 'send_telegram', $2, 'pending', $3)
        """,
        _ACTION_ID,
        {"chat_id": "42"},
        _REQUESTED_AT,
    )
    try:
        yield pool
    finally:
        await pool.close()


async def test_gate_park_owner_approve_executes_edits_and_preserves_provenance(
    approval_pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real parked action must execute, edit Telegram, and retain callback provenance."""

    async def original_tool(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("The original tool must not run before the owner approves it")

    monkeypatch.setattr(
        approvals_gate_module,
        "resolve_action_target_contact",
        AsyncMock(return_value=None),
    )
    park = _make_gate_wrapper(
        tool_name="telegram_send_message",
        original_fn=original_tool,
        pool=approval_pool,
        expiry_hours=72,
        risk_tier=ApprovalRiskTier.MEDIUM,
        rule_precedence=(),
        butler_name="relationship",
    )
    parked = await park(
        chat_id="42",
        text="Approved callback integration proof",
        _why="The owner requested this outbound message.",
        _evidence=[],
        _blast_radius="contact",
        _reversibility="compensable",
    )
    assert parked["status"] == "pending_approval"
    action_id = UUID(parked["action_id"])
    requested_at = await approval_pool.fetchval(
        "SELECT requested_at FROM pending_actions WHERE id = $1", action_id
    )

    module = ApprovalsModule()
    await module.on_startup(config=None, db=approval_pool)
    executed_calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_original_tool(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        executed_calls.append((tool_name, tool_args))
        return {"status": "sent", "message_id": "outbound-approval-proof"}

    module.set_tool_executor(execute_original_tool)
    mcp_client = _ExecutingButlerClient(module)
    app = _protected_approvals_app(approval_pool, mcp_mgr=_ExecutingMcpManager(mcp_client))
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
        action_id=action_id,
        verb="a",
        requested_at=requested_at,
        secret=_SECRET,
    )

    try:
        handled = await connector._maybe_handle_approval_callback(
            {
                "callback_query": {
                    "id": "cbq-approve",
                    "data": token,
                    "from": {"id": 9001},
                    "message": {"chat": {"id": 9001}, "message_id": 44},
                }
            }
        )
        detail = await http_client.get(
            f"http://dashboard-api:41200/api/approvals/{action_id}",
            headers={APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CALLBACK_CONNECTOR_TOKEN},
        )
    finally:
        await http_client.close()

    assert handled is True
    owner_resolver.assert_awaited_once_with(connector._db_pool, "telegram_bot", "9001")
    action = await approval_pool.fetchrow(
        "SELECT status, decided_by, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert action is not None
    assert action["status"] == "executed"
    assert action["decided_by"] == "human:owner@telegram"
    assert action["execution_result"]["success"] is True
    assert action["execution_result"]["result"] == {
        "status": "sent",
        "message_id": "outbound-approval-proof",
    }
    assert executed_calls == [
        (
            "telegram_send_message",
            {"chat_id": "42", "text": "Approved callback integration proof"},
        )
    ]
    assert mcp_client.calls == [("dispatch_approved_action", {"action_id": str(action_id)})]
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "executed"
    assert detail.json()["data"]["decided_by"] == "human:owner@telegram"
    audit = await approval_pool.fetchrow(
        "SELECT actor, action, target FROM public.audit_log WHERE target = $1", str(action_id)
    )
    assert audit is not None
    assert dict(audit) == {
        "actor": "human:owner@telegram",
        "action": "approval.approve",
        "target": str(action_id),
    }
    event_rows = await approval_pool.fetch(
        "SELECT event_type, actor FROM approval_events WHERE action_id = $1 ORDER BY occurred_at",
        action_id,
    )
    assert {(row["event_type"], row["actor"]) for row in event_rows} == {
        ("action_queued", "system:approval_gate"),
        ("action_approved", "human:owner@telegram"),
        ("action_execution_succeeded", "system:executor"),
    }
    assert [url.rsplit("/", 1)[-1] for url, _ in http_client.telegram_calls] == [
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
    ]
    assert http_client.telegram_calls[1][1] == {
        "chat_id": "9001",
        "message_id": 44,
        "text": "✅ Approved",
        "parse_mode": "HTML",
    }
    assert http_client.telegram_calls[2][1] == {
        "chat_id": "9001",
        "message_id": 44,
        "reply_markup": None,
    }


async def test_dashboard_notify_retry_holds_row_lock_against_abandon(
    approval_pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A notification side effect cannot be abandoned after delivery begins.

    This exercises the real dashboard retry and abandonment routes against
    PostgreSQL. The delivery stub pauses after the API has begun dispatching;
    Abandon must remain blocked on the executor's row lock until the delivery
    result and terminal execution event are durable.
    """
    action_id = uuid4()
    await approval_pool.execute(
        """
        INSERT INTO pending_actions (id, tool_name, tool_args, status)
        VALUES ($1, 'notify', $2, 'approved')
        """,
        action_id,
        {"channel": "telegram", "recipient": "42", "message": "Race-proof delivery"},
    )
    delivery_started = asyncio.Event()
    release_delivery = asyncio.Event()
    abandon_started = asyncio.Event()

    from butlers.modules.approvals import operations as approvals_operations

    real_abandon = approvals_operations.abandon_approved_action

    async def _observe_abandon(*args: Any, **kwargs: Any) -> dict[str, Any]:
        abandon_started.set()
        return await real_abandon(*args, **kwargs)

    monkeypatch.setattr(approvals_operations, "abandon_approved_action", _observe_abandon)

    class _BlockingDeliverClient:
        calls: list[tuple[str, dict[str, Any]]]

        def __init__(self) -> None:
            self.calls = []

        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            assert tool_name == "deliver"
            self.calls.append((tool_name, arguments))
            delivery_started.set()
            await release_delivery.wait()
            return SimpleNamespace(
                is_error=False,
                content=[SimpleNamespace(text=json.dumps({"message_id": "notify-race-proof"}))],
            )

    class _NotifyMcpManager:
        def __init__(self, client: _BlockingDeliverClient) -> None:
            self._client = client

        async def get_client(self, butler_name: str) -> _BlockingDeliverClient:
            assert butler_name == "switchboard"
            return self._client

    delivery_client = _BlockingDeliverClient()
    app = _protected_approvals_app(approval_pool, mcp_mgr=_NotifyMcpManager(delivery_client))
    headers = {"X-API-Key": _DASHBOARD_API_KEY}

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://dashboard-api:41200"
        ) as retry_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://dashboard-api:41200"
        ) as abandon_client,
    ):
        retry_task = asyncio.create_task(
            retry_client.post(f"/api/approvals/{action_id}/retry", headers=headers)
        )
        await asyncio.wait_for(delivery_started.wait(), timeout=2)
        abandon_task = asyncio.create_task(
            abandon_client.post(
                f"/api/approvals/{action_id}/abandon",
                json={"reason": "Owner changed their mind"},
                headers=headers,
            )
        )
        await asyncio.wait_for(abandon_started.wait(), timeout=2)

        # Give the Abandon route a scheduling turn. Before the fix it completes
        # here, marking the row abandoned while delivery is still in flight.
        await asyncio.sleep(0.05)
        abandon_blocked = not abandon_task.done()

        release_delivery.set()
        retry_response = await asyncio.wait_for(retry_task, timeout=2)
        abandon_response = await asyncio.wait_for(abandon_task, timeout=2)

    assert abandon_blocked, "Abandon must wait once notification delivery has begun"
    assert retry_response.status_code == 200, retry_response.text
    assert retry_response.json()["data"]["status"] == "executed"
    assert abandon_response.status_code == 409, abandon_response.text
    assert len(delivery_client.calls) == 1

    action = await approval_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert action is not None
    assert action["status"] == "executed"
    assert action["execution_result"]["success"] is True
    events = await approval_pool.fetch(
        "SELECT event_type FROM approval_events WHERE action_id = $1", action_id
    )
    assert {row["event_type"] for row in events} == {"action_execution_succeeded"}


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


async def test_non_primary_owner_tap_leaves_the_action_and_audit_stores_untouched(
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
    owner_resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), False))
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
    assert dict(action) == {"status": "pending", "decided_by": None}
    events = await approval_pool.fetch(
        "SELECT event_type, actor FROM approval_events WHERE action_id = $1", _ACTION_ID
    )
    assert events == []
    audit_rows = await approval_pool.fetch(
        "SELECT actor, action FROM public.audit_log WHERE target = $1", str(_ACTION_ID)
    )
    assert audit_rows == []
    assert [url.rsplit("/", 1)[-1] for url, _ in http_client.telegram_calls] == [
        "answerCallbackQuery"
    ]
    assert http_client.telegram_calls[0][1]["text"] == ""
    assert http_client.dashboard_calls == []


async def test_expired_owner_reject_tap_expires_without_human_decision_provenance(
    approval_pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await approval_pool.execute(
        "UPDATE pending_actions SET expires_at = now() - interval '1 minute' WHERE id = $1",
        _ACTION_ID,
    )
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
    action = await approval_pool.fetchrow(
        "SELECT status, decided_by FROM pending_actions WHERE id = $1", _ACTION_ID
    )
    assert dict(action) == {"status": "expired", "decided_by": "system:expiry"}
    events = await approval_pool.fetch(
        "SELECT event_type, actor FROM approval_events WHERE action_id = $1 ORDER BY occurred_at",
        _ACTION_ID,
    )
    assert [dict(event) for event in events] == [
        {"event_type": "action_expired", "actor": "system:expiry"}
    ]
    audit_rows = await approval_pool.fetch(
        "SELECT actor, action FROM public.audit_log WHERE target = $1", str(_ACTION_ID)
    )
    assert audit_rows == []
    assert [url.rsplit("/", 1)[-1] for url, _ in http_client.telegram_calls] == [
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
    ]
    assert http_client.telegram_calls[0][1]["text"] == "Already handled."
    assert [method for method, _, _ in http_client.dashboard_calls] == ["GET", "POST", "GET"]
    assert http_client.dashboard_calls[1][1].endswith(f"/api/approvals/{_ACTION_ID}/deny")
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
