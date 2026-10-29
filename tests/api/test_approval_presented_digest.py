"""A connector card tap is bound to the digest of the call the card showed."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from butlers.api.routers import approvals as approvals_router
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit

_ARGS = {"to": "dana@x.com", "subject": "Hi", "body": "Hello"}


class _Conn:
    def __init__(self, *, presented: Any = ()) -> None:
        self._presented = presented

    async def fetch(self, *_args: Any) -> Any:
        return self._presented


async def _check(conn: _Conn, stored: Any = _ARGS) -> None:
    await approvals_router._require_presented_call_unchanged(conn, uuid.uuid4(), stored)


async def test_matching_digest_passes() -> None:
    await _check(_Conn(presented=[{"args_digest": args_digest(_ARGS)}]))


async def test_any_mismatching_digest_refuses() -> None:
    stale = {"args_digest": args_digest({**_ARGS, "to": "other@x.com"})}
    with pytest.raises(HTTPException) as caught:
        await _check(_Conn(presented=[{"args_digest": args_digest(_ARGS)}, stale]))
    assert caught.value.status_code == 409


async def test_non_list_presented_rows_fail_closed() -> None:
    with pytest.raises(HTTPException) as caught:
        await _check(_Conn(presented=object()))
    assert caught.value.status_code == 409


@pytest.mark.parametrize("authenticated", [True, False])
async def test_only_connector_authenticated_approvals_run_the_card_check(
    monkeypatch: pytest.MonkeyPatch, authenticated: bool
) -> None:
    seen: dict[str, Any] = {}

    async def fake_owning(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "ok"

    async def fake_find(_db: Any, _action_id: Any) -> tuple[str, object]:
        return "general", object()

    monkeypatch.setattr(approvals_router, "approve_owning_action", fake_owning)
    monkeypatch.setattr(approvals_router, "_find_action_pool", fake_find)
    request = type(
        "Request",
        (),
        {"state": type("State", (), {"approval_callback_authenticated": authenticated})()},
    )()

    result = await approvals_router.approve_approval(
        str(uuid.uuid4()),
        request,  # type: ignore[arg-type]
        approvals_router.ApprovalApproveRequest(),
        db_mgr=object(),  # type: ignore[arg-type]
        mcp_mgr=object(),  # type: ignore[arg-type]
        callback_actor=approvals_router._TELEGRAM_CALLBACK_ACTOR if authenticated else None,
    )

    assert result == "ok"
    assert seen["check_presented_digests"] is authenticated
    # Connector taps use durable card digests; ordinary decisions must compare
    # their own displayed snapshot inside the owning transaction.
    assert seen["require_displayed_call"] is (not authenticated)
    assert seen["displayed_call"] is None
