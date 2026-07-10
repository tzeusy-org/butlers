"""Pin the roster/ marker-taxonomy contract in roster/conftest.py.

``roster/conftest.py::pytest_collection_modifyitems`` auto-marks every test
under ``roster/`` as ``integration`` (+ a Docker skipif) UNLESS the test (or
its class/module) already declares an explicit ``pytest.mark.unit``. That
explicit declaration is the author's taxonomy call and must be respected —
see bu-10fgt.2, where the hook silently overrode it and routed ~1,469 mocked
unit tests into the heavy Docker integration CI job.

This test exercises the hook function directly (no real pytest collection,
no Docker) so a future edit that regresses this contract fails loudly here
instead of silently re-routing tests between CI lanes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONFTEST_PATH = Path(__file__).parent / "conftest.py"


def _load_roster_conftest():
    """Load roster/conftest.py by file path (roster/ has no __init__.py)."""
    module_name = "roster_conftest_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, _CONFTEST_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_roster_conftest = _load_roster_conftest()


class _FakeItem:
    """Minimal stand-in for a pytest.Item — just enough for the hook under test."""

    def __init__(self, fspath: str, *, unit_marked: bool) -> None:
        self.fspath = fspath
        self._unit_marked = unit_marked
        self.added_markers: list[pytest.MarkDecorator] = []

    def get_closest_marker(self, name: str):
        if name == "unit" and self._unit_marked:
            return pytest.mark.unit.mark
        return None

    def add_marker(self, marker, append: bool = True) -> None:
        self.added_markers.append(marker)


def test_explicit_unit_marker_is_respected():
    """A roster test declaring ``pytest.mark.unit`` is not force-marked integration."""
    item = _FakeItem("/repo/roster/finance/tests/test_foo.py", unit_marked=True)

    _roster_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.added_markers == []


def test_unmarked_roster_test_still_gets_integration_and_docker_skip():
    """Default behaviour is unchanged for tests with no declared taxonomy."""
    item = _FakeItem("/repo/roster/finance/tests/test_foo.py", unit_marked=False)

    _roster_conftest.pytest_collection_modifyitems(config=None, items=[item])

    mark_names = {marker.mark.name for marker in item.added_markers}
    assert mark_names == {"integration", "skipif"}


def test_non_roster_test_is_left_untouched():
    """Tests outside roster/ are never touched by this hook."""
    item = _FakeItem("/repo/tests/test_bar.py", unit_marked=False)

    _roster_conftest.pytest_collection_modifyitems(config=None, items=[item])

    assert item.added_markers == []
