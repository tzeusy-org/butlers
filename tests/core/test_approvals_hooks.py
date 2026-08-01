"""Pool-isolation contracts for the optional approvals-module hooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.core import approvals_hooks

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_approval_hooks():
    """Keep the process-global compatibility slots hermetic between tests."""
    original_email = approvals_hooks._email_guard_hook
    original_recipient = approvals_hooks._recipient_guard_hook
    original_park = approvals_hooks._park_pending_action_hook
    yield
    approvals_hooks._email_guard_hook = original_email
    approvals_hooks._recipient_guard_hook = original_recipient
    approvals_hooks._park_pending_action_hook = original_park


async def test_pool_scoped_park_hook_does_not_leak_to_butler_without_approvals() -> None:
    """A module-enabled butler must not expose pending_actions to a foreign pool."""
    approvals_pool = object()
    finance_pool = object()
    park_hook = AsyncMock(return_value="parked")

    approvals_hooks.register_park_pending_action(park_hook, pool=approvals_pool)
    try:
        kwargs = {
            "action_id": uuid.uuid4(),
            "tool_name": "notify",
            "tool_args": {"channel": "telegram"},
            "agent_summary": "Missing channel identifier",
            "requested_at": datetime.now(UTC),
            "expires_at": None,
        }

        foreign_result = await approvals_hooks.park_pending_action(finance_pool, **kwargs)
        assert foreign_result is None
        park_hook.assert_not_awaited()

        owner_result = await approvals_hooks.park_pending_action(approvals_pool, **kwargs)
        assert owner_result == "parked"
        park_hook.assert_awaited_once()
    finally:
        approvals_hooks.unregister_approval_hooks(approvals_pool)


@pytest.mark.parametrize(
    ("register_name", "check_name", "target_kwargs"),
    [
        (
            "register_email_guard",
            "check_email_recipient",
            {"email_target": "contact@example.invalid"},
        ),
        (
            "register_recipient_guard",
            "check_recipient",
            {"channel": "telegram", "target": "12345"},
        ),
    ],
)
async def test_pool_scoped_recipient_guards_fail_open_only_for_foreign_pool(
    register_name: str,
    check_name: str,
    target_kwargs: dict[str, str],
) -> None:
    """An approvals-disabled pool must not inherit another butler's guard."""
    approvals_pool = object()
    finance_pool = object()
    decision = approvals_hooks.EmailGuardDecision(allowed=False, reason="parked")
    guard_hook = AsyncMock(return_value=decision)
    register = getattr(approvals_hooks, register_name)
    check = getattr(approvals_hooks, check_name)

    register(guard_hook, pool=approvals_pool)
    common_kwargs = {
        "rule_tool_name": "notify",
        "rule_match_args": {},
        "park_tool_name": "notify",
        "park_tool_args": {},
    }
    try:
        foreign_result = await check(finance_pool, **target_kwargs, **common_kwargs)
        assert foreign_result == approvals_hooks.EmailGuardDecision(
            allowed=True,
            reason="no_approvals_module",
        )
        guard_hook.assert_not_awaited()

        owner_result = await check(approvals_pool, **target_kwargs, **common_kwargs)
        assert owner_result == decision
        guard_hook.assert_awaited_once()
    finally:
        approvals_hooks.unregister_approval_hooks(approvals_pool)
