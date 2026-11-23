"""API surface of a failed or rejected approved send (bu-dhloi1.184)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from butlers.api.routers.approvals import (
    _execution_failure,
    _latest_execution_failure_code,
    _safe_dispatch_detail,
)
from butlers.modules.approvals.models import ActionStatus, PendingAction

pytestmark = pytest.mark.unit


def _action(status: ActionStatus, execution_result: dict | None = None) -> PendingAction:
    return PendingAction(
        id=uuid.uuid4(),
        tool_name="notify",
        tool_args={"channel": "telegram", "recipient": "42", "message": "m"},
        status=status,
        requested_at=datetime.now(UTC),
        execution_result=execution_result,
    )


def test_rejected_send_is_a_terminal_failure_from_the_executed_result() -> None:
    action = _action(
        ActionStatus.EXECUTED,
        {
            "success": True,
            "result": {
                "status": "delivery_rejected",
                "delivery_confirmed": False,
                "failure_code": "provider_rejected",
            },
        },
    )
    failure = _execution_failure(action)
    assert failure is not None
    assert (failure.code, failure.terminal) == ("provider_rejected", True)


def test_approved_unexecuted_send_reports_its_last_failure_code_as_retryable() -> None:
    failure = _execution_failure(_action(ActionStatus.APPROVED), "recipient_unavailable")
    assert failure is not None
    assert (failure.code, failure.terminal) == ("recipient_unavailable", False)


def test_success_unknown_outcome_and_unknown_codes_report_no_failure() -> None:
    assert _execution_failure(_action(ActionStatus.EXECUTED, {"success": True})) is None
    unknown = {"success": True, "result": {"status": "outcome_unknown"}}
    assert _execution_failure(_action(ActionStatus.EXECUTED, unknown)) is None
    assert _execution_failure(_action(ActionStatus.APPROVED), "chat_id='@x'") is None
    assert _execution_failure(_action(ActionStatus.PENDING), "policy_denied") is None


def test_dispatch_detail_names_the_failure_by_its_fixed_label_only() -> None:
    detail = _safe_dispatch_detail({"error": "send_failed:provider_rejected"})
    assert detail.startswith("the approved send failed: ")
    assert "provider" in detail
    assert "chat_id" not in _safe_dispatch_detail({"error": "chat_id='@Butlerly' not found"})


async def test_failure_code_lookup_degrades_to_none_when_events_are_unreadable() -> None:
    class _Conn:
        async def fetchrow(self, *_args):
            raise RuntimeError("no approval_events here")

    assert await _latest_execution_failure_code(_Conn(), uuid.uuid4()) is None
