"""Meta-test for the Dunbar-engine leak guard in this directory's ``conftest.py``.

The guard is a ``pytest_runtest_teardown`` hook, so nothing in a normal run
proves it still fires: if it were dropped, mis-registered, or ordered before
``monkeypatch``'s undo, every test would keep passing and the bu-poaxr class of
cross-file pollution would return silently.  These tests exercise the hook that
is actually registered for this directory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship import dunbar

pytestmark = pytest.mark.unit

_CONFTEST_NAME = "roster/relationship/tests/conftest.py"


def _registered_guard(request: pytest.FixtureRequest):
    """Return the leak-guard hook implementation registered by this conftest."""
    hook = request.config.pluginmanager.hook.pytest_runtest_teardown
    impls = [
        impl
        for impl in hook.get_hookimpls()
        if str(impl.plugin_name).replace("\\", "/").endswith(_CONFTEST_NAME)
    ]
    assert impls, f"no pytest_runtest_teardown hook registered from {_CONFTEST_NAME}"
    return impls[0]


def test_leak_guard_is_registered_last(request: pytest.FixtureRequest):
    """The guard must run after every fixture finalizer, hence ``trylast``."""
    assert _registered_guard(request).trylast is True


def test_leak_guard_accepts_a_restored_engine(request: pytest.FixtureRequest):
    """A test that leaves the engine untouched passes the guard."""
    _registered_guard(request).function(item=request.node)


def test_leak_guard_rejects_an_unrestored_engine(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    """A rebound engine global is reported, naming the leaking node and attribute."""
    monkeypatch.setattr(dunbar, "compute_tier_ranking", AsyncMock())

    with pytest.raises(AssertionError) as excinfo:
        _registered_guard(request).function(item=request.node)

    message = str(excinfo.value)
    assert "compute_tier_ranking" in message
    assert request.node.nodeid in message


def test_leak_guard_restores_the_engine_so_later_tests_are_not_punished(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    """After reporting a leak the guard puts the real callable back.

    A leak is sticky: nothing else restores the module global.  Without the
    repair the guard would fail the leaker and then every test after it, which
    is the mystery-failure-in-an-unrelated-file symptom it exists to abolish.
    Assert the repair directly, because a guard that only reports would still
    pass every test in this file.
    """
    pristine = dunbar.compute_tier_ranking
    monkeypatch.setattr(dunbar, "compute_tier_ranking", AsyncMock())

    with pytest.raises(AssertionError):
        _registered_guard(request).function(item=request.node)

    assert dunbar.compute_tier_ranking is pristine

    # A second, clean teardown now passes, so the run continues honestly.
    _registered_guard(request).function(item=request.node)
