"""Pool-isolation contracts for the optional approvals-module hooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core import approvals_hooks

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_approval_hooks():
    """Keep compatibility and pool-scoped hooks hermetic between tests."""
    original_email = approvals_hooks._email_guard_hook
    original_recipient = approvals_hooks._recipient_guard_hook
    original_park = approvals_hooks._park_pending_action_hook
    original_pool_hooks = dict(approvals_hooks._approval_hooks_by_pool)
    approvals_hooks._approval_hooks_by_pool.clear()
    yield
    approvals_hooks._email_guard_hook = original_email
    approvals_hooks._recipient_guard_hook = original_recipient
    approvals_hooks._park_pending_action_hook = original_park
    approvals_hooks._approval_hooks_by_pool.clear()
    approvals_hooks._approval_hooks_by_pool.update(original_pool_hooks)


async def test_pool_scoped_park_hook_does_not_leak_to_butler_without_approvals() -> None:
    """A module-enabled butler must not expose pending_actions to a foreign pool."""
    approvals_pool = object()
    finance_pool = object()
    park_hook = AsyncMock(return_value="parked")
    runtime = approvals_hooks.register_approval_hooks(
        approvals_pool,
        email_guard=AsyncMock(),
        recipient_guard=AsyncMock(),
        park_pending_action=park_hook,
    )

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
        approvals_hooks.unregister_approval_hooks(approvals_pool, runtime)


@pytest.mark.parametrize(
    ("runtime_field", "check_name", "target_kwargs"),
    [
        (
            "email_guard",
            "check_email_recipient",
            {"email_target": "contact@example.invalid"},
        ),
        (
            "recipient_guard",
            "check_recipient",
            {"channel": "telegram", "target": "12345"},
        ),
    ],
)
async def test_pool_scoped_recipient_guards_fail_open_only_for_foreign_pool(
    runtime_field: str,
    check_name: str,
    target_kwargs: dict[str, str],
) -> None:
    """An approvals-disabled pool must not inherit another butler's guard."""
    approvals_pool = object()
    finance_pool = object()
    decision = approvals_hooks.EmailGuardDecision(allowed=False, reason="parked")
    guard_hook = AsyncMock(return_value=decision)
    check = getattr(approvals_hooks, check_name)
    runtime = approvals_hooks.register_approval_hooks(
        approvals_pool,
        email_guard=guard_hook if runtime_field == "email_guard" else AsyncMock(),
        recipient_guard=guard_hook if runtime_field == "recipient_guard" else AsyncMock(),
        park_pending_action=AsyncMock(),
    )

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
        approvals_hooks.unregister_approval_hooks(approvals_pool, runtime)


async def test_older_module_shutdown_preserves_replacement_pool_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale module teardown must not remove a replacement's hook runtime."""
    from butlers.modules.approvals import email_guard, park
    from butlers.modules.approvals.module import ApprovalsModule

    pool = object()
    db = SimpleNamespace(pool=pool)
    old_module = ApprovalsModule()
    replacement_module = ApprovalsModule()

    old_email = AsyncMock(
        return_value=approvals_hooks.EmailGuardDecision(allowed=False, reason="old_email")
    )
    old_recipient = AsyncMock(
        return_value=approvals_hooks.EmailGuardDecision(allowed=False, reason="old_recipient")
    )
    old_park = AsyncMock(return_value="old_park")
    replacement_email = AsyncMock(
        return_value=approvals_hooks.EmailGuardDecision(allowed=False, reason="replacement_email")
    )
    replacement_recipient = AsyncMock(
        return_value=approvals_hooks.EmailGuardDecision(
            allowed=False,
            reason="replacement_recipient",
        )
    )
    replacement_park = AsyncMock(return_value="replacement_park")
    monkeypatch.setattr(approvals_hooks, "_email_guard_hook", None)
    monkeypatch.setattr(approvals_hooks, "_recipient_guard_hook", None)
    monkeypatch.setattr(approvals_hooks, "_park_pending_action_hook", None)
    monkeypatch.setattr(email_guard, "check_email_recipient", old_email)
    monkeypatch.setattr(email_guard, "check_recipient", old_recipient)
    monkeypatch.setattr(park, "park_pending_action", old_park)

    await old_module.on_startup(config=None, db=db)

    monkeypatch.setattr(email_guard, "check_email_recipient", replacement_email)
    monkeypatch.setattr(email_guard, "check_recipient", replacement_recipient)
    monkeypatch.setattr(park, "park_pending_action", replacement_park)
    await replacement_module.on_startup(config=None, db=db)

    try:
        await old_module.on_shutdown()

        common_kwargs = {
            "rule_tool_name": "notify",
            "rule_match_args": {},
            "park_tool_name": "notify",
            "park_tool_args": {},
        }
        email_result = await approvals_hooks.check_email_recipient(
            pool,
            email_target="contact@example.invalid",
            **common_kwargs,
        )
        recipient_result = await approvals_hooks.check_recipient(
            pool,
            channel="telegram",
            target="12345",
            **common_kwargs,
        )
        park_result = await approvals_hooks.park_pending_action(
            pool,
            action_id=uuid.uuid4(),
            tool_name="notify",
            tool_args={"channel": "telegram"},
            agent_summary="summary",
            requested_at=datetime.now(UTC),
            expires_at=None,
        )

        assert email_result.reason == "replacement_email"
        assert recipient_result.reason == "replacement_recipient"
        assert park_result == "replacement_park"
        old_email.assert_not_awaited()
        old_recipient.assert_not_awaited()
        old_park.assert_not_awaited()
        replacement_email.assert_awaited_once()
        replacement_recipient.assert_awaited_once()
        replacement_park.assert_awaited_once()
    finally:
        await replacement_module.on_shutdown()
