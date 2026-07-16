"""Real-Postgres regression: approvals JSONB writes must not double-encode.

bu-cymc4: gate.py and events.py used to pre-serialize dicts/lists with
``json.dumps()`` before binding them into ``pending_actions.tool_args``,
``pending_actions.evidence``, and ``approval_events.event_metadata`` — all
JSONB columns. Every asyncpg pool in this codebase registers a JSONB type
codec (``register_jsonb_codec``, src/butlers/db.py) whose encoder calls
``json.dumps()`` itself, so pre-serializing at the call site double-encoded
the value into a jsonb-typed STRING instead of an OBJECT/ARRAY (the same class
of regression as bu-qki26 / bu-aaacv / bu-qvnce.6 — see
tests/relationship/test_jsonb_codec.py).

The mocked-pool unit tests in test_module_approvals.py cannot catch this: they
only assert on the Python value handed to the mock, never round-trip through
real Postgres. These tests write via the real gate.py / events.py code paths
against a migrated-shape Postgres table and read the rows back directly.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.config import ApprovalRiskTier
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.gate import _make_gate_wrapper

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def _noop_original_fn(**kwargs: object) -> dict:
    """Stub original tool function; the park path never invokes it."""
    return {}


class TestGateParkPathJsonbRoundtrip:
    """gate.py's park-path INSERT must store tool_args/evidence as native types."""

    async def test_pending_action_tool_args_and_evidence_roundtrip_as_native_types(
        self, approvals_pool
    ) -> None:
        """A gated call with an unresolvable target parks with real dict/list columns.

        tool_args carries a nested dict/list to prove structured values
        survive the round-trip untouched (not flattened to a JSON string).

        """
        wrapper = _make_gate_wrapper(
            tool_name="test_tool",
            original_fn=_noop_original_fn,
            pool=approvals_pool,
            expiry_hours=1,
            risk_tier=ApprovalRiskTier.MEDIUM,
            rule_precedence=(),
            butler_name=None,
            tool_meta=None,
        )

        result = await wrapper(
            message="hello world",
            correlation_id="req-12345",
            context={"nested": {"depth": 2}, "tags": ["a", "b"]},
            why="testing the gate jsonb round-trip",
            evidence=["signal-one", "signal-two"],
        )

        # Unresolvable target (no recognized channel key in tool_args) with no
        # standing rule -> parked, not executed.
        assert result.get("status") == "pending_approval" or "action_id" in result

        row = await approvals_pool.fetchrow(
            "SELECT tool_args, evidence, why, status FROM pending_actions "
            "WHERE tool_name = 'test_tool'"
        )
        assert row is not None, "gate wrapper did not insert a pending_actions row"

        stored_tool_args = row["tool_args"]
        assert isinstance(stored_tool_args, dict), (
            f"tool_args arrived as {type(stored_tool_args).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_tool_args["message"] == "hello world"
        assert stored_tool_args["correlation_id"] == "req-12345"
        assert stored_tool_args["context"] == {"nested": {"depth": 2}, "tags": ["a", "b"]}

        stored_evidence = row["evidence"]
        assert isinstance(stored_evidence, list), (
            f"evidence arrived as {type(stored_evidence).__name__!r}, not a list — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_evidence == ["signal-one", "signal-two"]
        assert row["status"] == "pending"


class TestRecordApprovalEventJsonbRoundtrip:
    """events.py's record_approval_event() must store event_metadata as a dict."""

    async def test_event_metadata_roundtrips_as_dict_not_double_encoded_string(
        self, approvals_pool
    ) -> None:
        action_id = uuid.uuid4()
        # Seed the referenced pending_actions row (approval_events.action_id FKs to it).
        await approvals_pool.execute(
            "INSERT INTO pending_actions (id, tool_name, tool_args, status) "
            "VALUES ($1, $2, $3, $4)",
            action_id,
            "test_tool",
            {"foo": "bar"},
            "pending",
        )

        non_json_safe_id = uuid.uuid4()
        await record_approval_event(
            approvals_pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
            reason="gated invocation intercepted",
            metadata={"tool_name": "test_tool", "request_id": non_json_safe_id},
            occurred_at=datetime.now(UTC),
        )

        row = await approvals_pool.fetchrow(
            "SELECT event_metadata FROM approval_events WHERE action_id = $1",
            action_id,
        )
        assert row is not None
        stored_metadata = row["event_metadata"]
        assert isinstance(stored_metadata, dict), (
            f"event_metadata arrived as {type(stored_metadata).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_metadata == {
            "tool_name": "test_tool",
            "request_id": str(non_json_safe_id),
        }
