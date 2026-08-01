"""Shared test configuration for relationship butler unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _register_real_approval_hooks(request: pytest.FixtureRequest):
    """Register real approvals hooks when a relationship test declares a pool fixture."""
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )
    from butlers.modules.approvals.park import park_pending_action as _real_park

    pool_name = next(
        (name for name in ("pool", "relationship_pool") if name in request.fixturenames),
        None,
    )
    if pool_name is None:
        yield
        return

    pool = request.getfixturevalue(pool_name)
    runtime = _hooks.register_approval_hooks(
        pool,
        email_guard=check_email_recipient,
        recipient_guard=check_recipient,
        park_pending_action=_real_park,
    )
    yield
    _hooks.unregister_approval_hooks(pool, runtime)
