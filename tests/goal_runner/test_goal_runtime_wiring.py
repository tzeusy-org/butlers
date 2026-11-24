"""The switchboard daemon starts goal supervision only for a switched-on runtime (bu-dhloi1.206)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from butlers.goal_runner.control_plane import GoalRuntime, GoalRuntimeSwitch
from butlers.lifecycle import _start_goal_runtime

pytestmark = pytest.mark.unit


def _runtime(enabled: bool) -> MagicMock:
    runtime = MagicMock(spec=GoalRuntime)
    runtime.switch = GoalRuntimeSwitch(enabled=enabled)
    return runtime


def test_a_daemon_without_a_goal_runtime_starts_nothing():
    _start_goal_runtime(SimpleNamespace(goal_runtime=None))
    _start_goal_runtime(SimpleNamespace())


def test_a_switched_off_runtime_is_not_started():
    runtime = _runtime(enabled=False)
    _start_goal_runtime(SimpleNamespace(goal_runtime=runtime))
    runtime.start.assert_not_called()


def test_a_switched_on_runtime_starts_supervising():
    runtime = _runtime(enabled=True)
    _start_goal_runtime(SimpleNamespace(goal_runtime=runtime))
    runtime.start.assert_called_once_with()


def test_the_switch_is_off_by_default():
    assert GoalRuntimeSwitch().enabled is False
    assert GoalRuntime(MagicMock(), MagicMock()).switch.enabled is False
