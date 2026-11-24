"""Closed, content-free failure codes of an approved send (bu-dhloi1.184)."""

from __future__ import annotations

import pytest

from butlers.modules.approvals.delivery_failure import (
    SEND_FAILURE_LABELS,
    SendFailure,
    delivery_rejected_result,
    failure_from_execution_result,
    failure_payload,
    is_egress_tool,
    provider_rejected,
    send_failure_code,
)

pytestmark = pytest.mark.unit


def _failed(outcome: str, detail: str) -> dict:
    return {"error": "boom", "transport": {"outcome": outcome, "error_detail": detail}}


@pytest.mark.parametrize("tool", ["notify", "telegram_send_message", "email_reply_to_thread"])
def test_outbound_sends_are_egress(tool: str) -> None:
    assert is_egress_tool(tool)


@pytest.mark.parametrize("tool", ["relationship_assert_fact", "telegram_react_to_message"])
def test_other_tools_are_not_egress(tool: str) -> None:
    assert not is_egress_tool(tool)


def test_code_is_read_from_the_transport_fragment_through_wrappers() -> None:
    nested = {"result": {"notify_response": _failed("rejected", "provider_rejected")}}
    assert send_failure_code(nested) == "provider_rejected"
    assert provider_rejected(nested)


def test_unknown_or_missing_detail_falls_back_to_the_generic_code() -> None:
    assert send_failure_code(_failed("rejected", "chat_id='@x' not found")) == "send_failed"
    assert send_failure_code({"error": "no transport"}) == "send_failed"
    assert not provider_rejected(_failed("not_attempted", "recipient_unavailable"))


def test_uncertain_transport_codes_are_not_failure_codes() -> None:
    # An ambiguous send is outcome_unknown, never a retryable "send failed" code.
    assert send_failure_code(_failed("uncertain", "transport_timeout")) == "send_failed"
    assert SendFailure("worker_recovery").code == "send_failed"


def test_send_failure_text_is_only_the_code() -> None:
    assert str(SendFailure("policy_denied")) == "send_failed:policy_denied"
    assert SendFailure("chat_id='@x'").code == "send_failed"


def test_rejected_result_round_trips_to_a_payload_and_never_unknown_text() -> None:
    result = {"success": True, "result": delivery_rejected_result("provider_rejected")}
    assert failure_from_execution_result(result) == {
        "code": "provider_rejected",
        "label": SEND_FAILURE_LABELS["provider_rejected"],
    }
    assert failure_from_execution_result({"result": {"status": "sent"}}) is None
    assert failure_from_execution_result({"result": {"status": "outcome_unknown"}}) is None
    assert failure_payload("free text from a provider") is None
