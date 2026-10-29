"""bu-q7vx1q.88.3: the Gmail connector honors a block rule's "except people I know" option.

Drives the real ``_ingest_single_message`` with the real connector policy evaluator and a real
known-contact snapshot (only the network edges are patched). Only a loaded snapshot may exempt a
sender; an unloaded one leaves the rule in force and the drop keeps its unknown classification.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime

_KNOWN = "alice@known.example"


@pytest.fixture
def runtime() -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )
    return GmailConnectorRuntime(config, cursor_pool=MagicMock())


def _message(from_addr: str) -> dict[str, Any]:
    return {
        "id": "msg1",
        "threadId": "thread1",
        "internalDate": "1708000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": "Hello"},
                {"name": "Message-ID", "value": "<m1@example.com>"},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"body").decode()},
        },
    }


def _block_rule(**condition: Any) -> dict[str, Any]:
    return {
        "id": "rule-1",
        "rule_type": "sender_domain",
        "condition": {"domain": "known.example", "match": "exact", **condition},
        "action": "block",
        "priority": 10,
        "name": None,
        "created_at": "2026-02-01T00:00:00Z",
        "created_by": "dashboard",
        "promoted_from_suggestion_id": None,
    }


async def _ingest(
    runtime: GmailConnectorRuntime, *, loaded: bool, rule: dict[str, Any]
) -> AsyncMock:
    evaluator = runtime._gmail_policy_evaluator
    if loaded:
        evaluator._db_pool = AsyncMock()
        evaluator._db_pool.fetch.return_value = [{"value": _KNOWN}]
        await evaluator.get_snapshot()
    runtime._ingestion_policy._last_loaded_at = time.monotonic()
    runtime._ingestion_policy._rules = [rule]
    submit = AsyncMock()
    with (
        patch.object(
            runtime, "_fetch_message", new_callable=AsyncMock, return_value=_message(_KNOWN)
        ),
        patch.object(runtime, "_build_ingest_envelope", new_callable=AsyncMock),
        patch.object(runtime, "_submit_to_ingest_api", submit),
    ):
        await runtime._ingest_single_message("msg1")
    return submit


async def test_a_known_contact_passes_a_flagged_block_rule_and_is_ingested(runtime) -> None:
    submit = await _ingest(runtime, loaded=True, rule=_block_rule(except_known_contacts=True))

    assert submit.await_count == 1
    assert len(runtime._filtered_event_buffer._rows) == 0


async def test_an_unflagged_block_rule_still_drops_and_marks_a_known_contact(runtime) -> None:
    submit = await _ingest(runtime, loaded=True, rule=_block_rule())

    assert submit.await_count == 0
    (row,) = runtime._filtered_event_buffer._rows
    assert row[9]["drop_context"]["important_dropped"] is True


async def test_an_unloaded_snapshot_leaves_the_rule_in_force_with_unknown_classification(
    runtime,
) -> None:
    submit = await _ingest(runtime, loaded=False, rule=_block_rule(except_known_contacts=True))

    assert submit.await_count == 0
    (row,) = runtime._filtered_event_buffer._rows
    assert row[9]["drop_context"]["classification"]["state"] == "unloaded"
    assert "important_dropped" not in row[9]["drop_context"]
