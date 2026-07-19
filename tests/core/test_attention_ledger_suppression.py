"""Unit tests for the shared owner-notify suppression gate (bu-gts7r).

``check_owner_notify_suppression`` was extracted from the near-verbatim copies in
``butlers.jobs.secrets_lifecycle._check_suppression`` and
``butlers.jobs.home._check_owner_notify_suppression``. It replicates notify()'s
owner-default gate: quiet hours first, then the context-bus dnd/sleeping signal.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.attention_ledger import check_owner_notify_suppression

pytestmark = pytest.mark.unit

_QUIET_POLICY = {"quiet_start_hour": 0, "quiet_end_hour": 23, "timezone": "UTC"}


async def test_quiet_hours_active_returns_quiet_hours() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_QUIET_POLICY),
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", return_value=True),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "quiet_hours"


async def test_context_bus_active_returns_context_bus_signal() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value="dnd"),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "context_bus:dnd"


async def test_neither_returns_none() -> None:
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") is None


async def test_both_active_quiet_hours_takes_precedence() -> None:
    """Quiet hours is checked first, so it wins over a concurrent context signal
    and the context-bus lookup is never consulted."""
    context_lookup = AsyncMock(return_value="dnd")
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=_QUIET_POLICY),
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", return_value=True),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=context_lookup,
        ),
    ):
        assert await check_owner_notify_suppression(object(), log_context="t") == "quiet_hours"
    context_lookup.assert_not_awaited()


async def test_quiet_hours_lookup_failure_is_non_fatal_and_logs_context(caplog) -> None:
    """A quiet-hours lookup that raises is swallowed (falls through to the
    context-bus check) and logs a debug line prefixed with the caller's context."""
    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new=AsyncMock(side_effect=RuntimeError("policy DB down")),
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
        caplog.at_level(logging.DEBUG, logger="butlers.core.attention_ledger"),
    ):
        result = await check_owner_notify_suppression(
            object(), log_context="secrets_lifecycle_check"
        )

    assert result is None
    assert any(
        "secrets_lifecycle_check: quiet-hours policy lookup failed" in r.message
        for r in caplog.records
    )
