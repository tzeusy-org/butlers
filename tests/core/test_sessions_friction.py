"""Unit tests for butlers.core.sessions friction-ledger derivation (bu-8cdl1.9 S2).

Covers the deterministic classification of a completed session into a typed
friction kind, and the failure-isolated write path: a friction-insert error
must be logged and swallowed, never propagated out of the append-only
session-close contract (``session_complete``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core.sessions import _classify_friction_kind, _record_friction_event
from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("success", "error", "model", "expected"),
    [
        (True, None, "claude-3", None),
        (True, "", "claude-3", None),
        (True, "transient ToolError: retried and recovered", "claude-3", "recovered_error"),
        (
            False,
            "RuntimeError: degenerate_tool_loop: 5 consecutive identical calls to foo",
            "claude-3",
            "degenerate_tool_loop",
        ),
        (False, "GuardrailError: tool_call_budget_exceeded", "claude-3", "guardrail_termination"),
        (False, "GuardrailError: token_budget_exceeded", "claude-3", "guardrail_termination"),
        (
            False,
            "TimeoutError: Session timed out after 45s "
            "(model=claude-haiku-4-5-mini, butler=switchboard)",
            "claude-haiku-4-5-mini",
            "classification_timeout",
        ),
        (
            # Same message template but a non-"mini" model and non-classification
            # dispatch must not be misclassified as classification_timeout.
            False,
            "TimeoutError: Session timed out after 30s "
            "(model=claude-sonnet-4-6, butler=switchboard)",
            "claude-sonnet-4-6",
            "dead_end",
        ),
        (False, "ValueError: something unrelated broke", "claude-3", "dead_end"),
        (False, None, "claude-3", "dead_end"),
    ],
)
def test_classify_friction_kind(success, error, model, expected):
    assert _classify_friction_kind(success=success, error=error, model=model) == expected


async def test_record_friction_event_writes_one_row_for_a_classified_kind():
    pool = fake_pool(execute="INSERT 0 1")
    error = "RuntimeError: degenerate_tool_loop: 5 consecutive identical calls to foo"

    await _record_friction_event(pool, "session-1", success=False, error=error, model="claude-3")

    query, *args = pool.execute.await_args.args
    pool.execute.assert_awaited_once()
    assert "INSERT INTO sessions_friction" in query
    # (session_id, kind, detail): the classified kind, with the raw error as detail.
    assert args == ["session-1", "degenerate_tool_loop", error]


async def test_record_friction_event_is_a_no_op_for_a_clean_session():
    pool = fake_pool()

    await _record_friction_event(pool, "session-1", success=True, error=None, model="claude-3")

    pool.execute.assert_not_awaited()


async def test_record_friction_event_swallows_write_failure():
    """A friction-write failure must never propagate out of session close."""
    pool = fake_pool(
        execute=AsyncMock(side_effect=RuntimeError('relation "sessions_friction" does not exist'))
    )

    await _record_friction_event(  # must not raise
        pool, "session-1", success=False, error="degenerate_tool_loop", model="claude-3"
    )

    pool.execute.assert_awaited_once()  # the write was attempted
