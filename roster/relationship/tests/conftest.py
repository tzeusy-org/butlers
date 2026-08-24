"""Shared test configuration for relationship butler tool/job tests."""

from __future__ import annotations

import pytest

from butlers.tools.relationship import dunbar as _dunbar_engine

#: Pristine callables of the Dunbar scoring engine, snapshotted at collection
#: time (before any test body has had a chance to patch them).
_PRISTINE_DUNBAR_GLOBALS: dict[str, object] = {
    name: value for name, value in vars(_dunbar_engine).items() if callable(value)
}


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Fail any test that leaves the Dunbar scoring engine's globals patched.

    The relationship router and ``contact_get`` both import the engine lazily
    and resolve ``compute_tier_ranking`` (and friends) through the module global
    at call time.  A test that rebinds one of those attributes without restoring
    it redirects every later caller in the same process to a stale mock — the
    contact under test is absent from the mocked ranking, so ``contact_get``
    silently reports tier 1500 / score 0.0 for a contact that genuinely has
    interactions (bu-poaxr).  The symptom surfaces in an unrelated test file
    much later in the run, so pin the failure on the leaker instead.

    Use ``monkeypatch.setattr`` (pytest restores it at teardown) rather than
    assigning the module attribute directly.  This runs as a ``trylast``
    teardown hook rather than an autouse fixture because fixture finalizers —
    including ``monkeypatch``'s undo — must all have run before the check.
    """
    current = vars(_dunbar_engine)
    leaked = sorted(
        name
        for name, pristine in _PRISTINE_DUNBAR_GLOBALS.items()
        if current.get(name) is not pristine
    )
    # Repair before reporting.  A leak is sticky by construction -- nothing else
    # will put the real callable back -- so without this the guard would fail the
    # leaker AND every test that runs after it, burying the one useful nodeid
    # under a wall of errors in unrelated files.  That is the same
    # mystery-failure-elsewhere symptom the guard exists to abolish.
    for name in leaked:
        setattr(_dunbar_engine, name, _PRISTINE_DUNBAR_GLOBALS[name])
    assert not leaked, (
        f"{item.nodeid} leaked a patch on butlers.tools.relationship.dunbar: "
        f"{', '.join(leaked)}. Use monkeypatch.setattr so pytest restores it. "
        f"The engine has been restored so the rest of this run stays honest; "
        f"this failure names the leaker, not a victim."
    )


@pytest.fixture(autouse=True)
def _register_real_approval_hooks(request: pytest.FixtureRequest):
    """Register real approvals hooks for a relationship test's exact pool fixture."""
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient,
        check_recipient,
    )
    from butlers.modules.approvals.park import park_pending_action as _real_park

    pool_fixture_names = (
        "pool",
        "pool_with_relational_predicates",
        "pa_pool",
        "frc_pool",
        "tier_pool",
        "dunbar_pool",
        "simple_pool",
    )
    pool_name = next((name for name in pool_fixture_names if name in request.fixturenames), None)
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
