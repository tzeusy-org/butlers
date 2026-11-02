"""attachment_materialize serves only the routed request's own lazy refs (bu-jv1moa.59)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    reset_current_runtime_session_locator_unbound,
    set_current_runtime_session_id,
    set_current_runtime_session_locator_unbound,
    set_runtime_session_routing_context,
)
from butlers.tools.attachment_materialize import materialize_routed_attachment

_SESSION = "sw-session-1"
_LAZY = {"filename": "a.pdf", "source_message_id": "m1", "source_attachment_id": "a1"}
_FETCHED = {
    "fetched": True,
    "blob_ref": "s3://b/connectors/a.pdf",
    "fetch_error": None,
    "filename": "a.pdf",
    "media_type": "application/pdf",
    "size_bytes": 10,
}


async def _call(context: dict[str, Any] | None, message_id: str, attachment_id: str, **binding):
    pool = AsyncMock()
    pool.fetchrow.return_value = _FETCHED
    session_token = set_current_runtime_session_id(binding.get("session", _SESSION))
    unbound_token = set_current_runtime_session_locator_unbound(binding.get("unbound", False))
    set_runtime_session_routing_context(_SESSION, context)
    try:
        result = await materialize_routed_attachment(pool, message_id, attachment_id, wait_s=0)
    finally:
        clear_runtime_session_routing_context(_SESSION)
        reset_current_runtime_session_locator_unbound(unbound_token)
        reset_current_runtime_session_id(session_token)
    return result, pool


async def test_a_ref_of_the_routed_request_is_materialized() -> None:
    result, pool = await _call({"attachments": [{"storage_ref": "s3://x"}, _LAZY]}, "m1", "a1")

    assert result["status"] == "ready"
    assert result["storage_ref"] == _FETCHED["blob_ref"]
    pool.fetchrow.assert_awaited()


@pytest.mark.parametrize(
    ("context", "message_id", "attachment_id", "binding"),
    [
        pytest.param({"attachments": [_LAZY]}, "m1", "a2", {}, id="other-attachment"),
        pytest.param({"attachments": [_LAZY]}, "m9", "a1", {}, id="other-message"),
        pytest.param({"attachments": [_LAZY]}, "m1", "a1", {"unbound": True}, id="locator-only"),
        pytest.param({"attachments": [_LAZY]}, "m1", "a1", {"session": None}, id="no-session"),
        pytest.param({"request_id": "r1"}, "m1", "a1", {}, id="no-attachments"),
        pytest.param(None, "m1", "a1", {}, id="no-routing-context"),
        pytest.param({"attachments": [{"source_message_id": "m1"}]}, "m1", "a1", {}, id="half-ref"),
    ],
)
async def test_an_unbound_ref_is_refused_before_the_database(
    context: dict[str, Any] | None, message_id: str, attachment_id: str, binding: dict[str, Any]
) -> None:
    result, pool = await _call(context, message_id, attachment_id, **binding)

    assert result == {
        "status": "refused",
        "reason": "attachment_not_in_request",
        "message_id": message_id,
        "attachment_id": attachment_id,
        "hint": "Only attachments of the message this session is handling can be fetched.",
    }
    assert pool.mock_calls == []
