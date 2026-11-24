"""Dashboard Approve/Reject through the od1 route keep the displayed-call binding.

Software HTTP/transaction controls with a fake pool, not PostgreSQL evidence; the real
redemption is proven in ``tests/integration/test_owner_delivery_ledger.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.api.routers import approvals as routes
from butlers.api.routers import owner_delivery as od_routes
from butlers.core.owner_delivery import mint_token
from butlers.modules.approvals.previews import dashboard_call_preview

pytestmark = pytest.mark.unit

_CARD_ARGS = {"chat_id": "synthetic", "text": "Owner reviewed this"}


class _Conn:
    _audit_depth = 0

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self.row = {"tool_name": tool_name, "tool_args": tool_args, "goal_id": None}

    def is_in_transaction(self) -> bool:
        return self._audit_depth > 0

    @asynccontextmanager
    async def transaction(self):
        self._audit_depth += 1
        try:
            yield
        finally:
            self._audit_depth -= 1

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        return self.row


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _wire(
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    action: str,
    tool_name: str = "telegram_send_message",
    tool_args: dict[str, Any] | None = None,
    connector: bool = False,
) -> AsyncMock:
    """Route one token to a fake pool; returns the redemption spy, which refuses."""
    action_id = uuid4()
    pool = _Pool(_Conn(tool_name, tool_args if tool_args is not None else _CARD_ARGS))

    async def locate(_db: Any, _verifier: str) -> tuple[str, Any, dict[str, str]]:
        return (
            "general",
            pool,
            {"action": action, "kind": "approval", "source_ref": str(action_id)},
        )

    async def find(*_args: Any) -> tuple[str, Any]:
        return "general", pool

    monkeypatch.setattr(od_routes, "_locate", locate)
    monkeypatch.setattr(od_routes, "_find_action_pool", find)
    monkeypatch.setattr(od_routes, "_callback_authenticated", lambda _request: connector)
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *a: None)
    redeem = AsyncMock(return_value={"error": "owner_delivery_refused", "reason": "spy"})
    monkeypatch.setattr(routes, "_redeem_owner_delivery", redeem)
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[routes.get_mcp_manager] = lambda: object()
    return redeem


async def _post(app: Any, body: dict[str, Any], **headers: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/api/owner-delivery/actions", json=body, headers=headers)


@pytest.mark.parametrize(
    "tool_name, tool_args, displayed",
    [
        pytest.param(
            "telegram_send_message",
            _CARD_ARGS,
            None,
            id="the call was never echoed",
        ),
        pytest.param(
            "telegram_send_message",
            {**_CARD_ARGS, "text": "A later unreviewed call"},
            dashboard_call_preview("telegram_send_message", _CARD_ARGS)["displayed_call"],
            id="the call changed since shown",
        ),
        pytest.param(
            "relationship_assert_fact",
            {"ordinal": 0},
            {"tool_name": "relationship_assert_fact", "args_digest": "a" * 64},
            id="an open-only call has nothing to display",
        ),
    ],
)
async def test_dashboard_token_cannot_approve_a_call_the_owner_was_not_shown(
    app, monkeypatch, tool_name, tool_args, displayed
) -> None:
    redeem = _wire(app, monkeypatch, action="approve", tool_name=tool_name, tool_args=tool_args)
    token, _ = mint_token()

    response = await _post(app, {"token": token, "displayed_call": displayed})

    assert response.status_code == 409
    assert response.json()["detail"] == routes.APPROVAL_CALL_CHANGED_DETAIL
    # Refused before redemption: the token stays unconsumed and nothing was decided.
    redeem.assert_not_awaited()


async def test_dashboard_token_approves_the_displayed_call(app, monkeypatch) -> None:
    redeem = _wire(app, monkeypatch, action="approve")
    token, _ = mint_token()
    shown = dashboard_call_preview("telegram_send_message", _CARD_ARGS)["displayed_call"]

    response = await _post(app, {"token": token, "displayed_call": shown})

    assert response.status_code == 409 and response.json()["detail"]["reason"] == "spy"
    assert redeem.await_args.kwargs["token"] == token
    assert redeem.await_args.kwargs["surface"] == "dashboard"


async def test_telegram_card_shows_its_call_itself_and_needs_no_echo(app, monkeypatch) -> None:
    redeem = _wire(app, monkeypatch, action="approve", connector=True)
    token, _ = mint_token()

    response = await _post(app, {"token": token}, **{"X-Butlers-Decision-Actor": "owner@telegram"})

    assert response.json()["detail"]["reason"] == "spy"
    assert redeem.await_args.kwargs["surface"] == "telegram"


async def test_dashboard_token_deny_carries_the_owners_reason(app, monkeypatch) -> None:
    redeem = _wire(app, monkeypatch, action="reject")
    token, _ = mint_token()

    await _post(app, {"token": token, "reason": "Not this one"})

    assert redeem.await_args.kwargs["audit_note"] == "Not this one"


async def test_connector_token_cannot_add_a_denial_reason(app, monkeypatch) -> None:
    redeem = _wire(app, monkeypatch, action="reject", connector=True)
    token, _ = mint_token()

    response = await _post(
        app,
        {"token": token, "reason": "forged"},
        **{"X-Butlers-Decision-Actor": "owner@telegram"},
    )

    assert response.status_code == 403
    redeem.assert_not_awaited()
