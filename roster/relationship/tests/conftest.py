"""Shared test configuration for relationship butler tool/job tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _register_real_park_pending_action_hook():
    """Register the real ``park_pending_action`` hook for every relationship test.

    ``relationship_assert_fact.py`` and ``relationship_jobs.py`` route every
    PENDING ``pending_actions`` insert through
    ``butlers.core.approvals_hooks.park_pending_action`` (bu-g27ib), which
    delegates to whatever ``modules.approvals.park.park_pending_action``
    implementation the approvals module registered during ``on_startup``.
    These tests call the relationship library/job functions directly against
    a real DB pool -- no daemon starts, so ``on_startup`` never runs and the
    hook slot is empty by default (a loud no-op, per the hook's docstring).
    Registering the real implementation here mirrors what the approvals
    module's ``on_startup`` does in production, and restoring the prior
    value afterward keeps this from leaking into other tests sharing the
    same xdist worker process (mirrors the root ``conftest.py``'s
    ``_restore_approvals_guard_hooks`` pattern for the sibling email/
    recipient guard hooks).
    """
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.park import park_pending_action as _real_park

    orig = _hooks._park_pending_action_hook
    _hooks._park_pending_action_hook = _real_park
    yield
    _hooks._park_pending_action_hook = orig
