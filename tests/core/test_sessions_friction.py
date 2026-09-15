"""Unit tests for butlers.core.sessions friction-ledger derivation (bu-8cdl1.9 S2).

Covers the deterministic classification of a completed session into a typed
friction kind, and the failure-isolated write path: a friction-insert error
must be logged and swallowed, never propagated out of the append-only
session-close contract (``session_complete``).
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.core.sessions import _classify_friction_kind, _record_friction_event

pytestmark = pytest.mark.unit


class _FakePool:
    """Fake asyncpg pool capturing execute() calls, optionally raising."""

    def __init__(self, *, raise_on_execute: Exception | None = None) -> None:
        self._raise_on_execute = raise_on_execute
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        return "INSERT 0 1"


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
    pool = _FakePool()

    await _record_friction_event(
        pool, "session-1", success=False, error="degenerate_tool_loop", model="claude-3"
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "sessions_friction" in query
    assert args == ("session-1", "degenerate_tool_loop", "degenerate_tool_loop")


async def test_record_friction_event_is_a_no_op_for_a_clean_session():
    pool = _FakePool()

    await _record_friction_event(pool, "session-1", success=True, error=None, model="claude-3")

    assert pool.execute_calls == []


async def test_record_friction_event_swallows_write_failure():
    """A friction-write failure must never propagate out of session close."""
    pool = _FakePool(raise_on_execute=RuntimeError('relation "sessions_friction" does not exist'))

    await _record_friction_event(  # must not raise
        pool, "session-1", success=False, error="degenerate_tool_loop", model="claude-3"
    )

    assert len(pool.execute_calls) == 1  # the write was attempted
