"""Unit coverage for ``register_continuity_tools``' staffer scoping (bu-2jtfw.13).

``carry_forward`` backs the task-continuity ledger for PROMPT-mode recurring
tasks. Every staffer butler (concierge, messenger, switchboard, qa) declares
only job-mode schedules, so the tool has no reachable caller there. This
role-fit and reachability constraint mirrors the existing non-STAFFER scoping
already drawn around other narrative/dispatch-adjacent core tools (``notify``,
the ``temporal`` group); it is independent of RFC 0002 Amendment 1 / RFC 0027's
initial model-context working-set target.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._continuity import register_continuity_tools

pytestmark = pytest.mark.unit


def _record_registrations(butler_type: ButlerType) -> list[tuple[str, str]]:
    registrations: list[tuple[str, str]] = []

    def core_tool(group: str, **tool_kwargs):
        def register(fn):
            registrations.append((tool_kwargs.get("name", fn.__name__), group))
            return fn

        return register

    register_continuity_tools(
        ToolContext(
            daemon=MagicMock(),
            pool=MagicMock(),
            spawner=MagicMock(),
            butler_name="general",
            butler_type=butler_type,
            is_switchboard=False,
            is_messenger=False,
            route_metrics=MagicMock(),
        ),
        MagicMock(),
        core_tool,
    )
    return registrations


def test_carry_forward_registered_for_domain_butlers() -> None:
    assert ("carry_forward", "continuity") in _record_registrations(ButlerType.BUTLER)


def test_carry_forward_not_registered_for_staffers() -> None:
    assert _record_registrations(ButlerType.STAFFER) == []
