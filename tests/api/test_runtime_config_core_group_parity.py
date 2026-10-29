"""KNOWN_CORE_GROUPS cannot drift from the core groups the daemon registers (bu-5lp0io).

A PATCH naming a registered group (``continuity`` was the one that drifted) must not 422.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers.api.routers.runtime_config import KNOWN_CORE_GROUPS, RuntimeConfigPatch

pytestmark = pytest.mark.unit

_CORE_TOOLS = Path(__file__).resolve().parents[2] / "src" / "butlers" / "core_tools"
_GROUP_LITERAL = re.compile(r'_core_tool\(\s*"([a-z_]+)"')


def _registered_core_groups() -> set[str]:
    groups: set[str] = set()
    for path in _CORE_TOOLS.glob("*.py"):
        groups.update(_GROUP_LITERAL.findall(path.read_text()))
    return groups


def test_known_core_groups_match_the_registered_core_groups() -> None:
    registered = _registered_core_groups()

    assert registered, "no _core_tool(...) registrations found; the scan is broken"
    assert KNOWN_CORE_GROUPS == registered


def test_every_registered_core_group_is_accepted_by_patch_validation() -> None:
    for group in sorted(_registered_core_groups()):
        assert RuntimeConfigPatch(core_groups=[group]).core_groups == [group]


def test_continuity_is_a_known_core_group() -> None:
    assert "continuity" in KNOWN_CORE_GROUPS
    assert RuntimeConfigPatch(core_groups=["infra", "continuity"]).core_groups == [
        "infra",
        "continuity",
    ]


def test_unknown_group_is_still_rejected_and_lists_continuity() -> None:
    with pytest.raises(ValueError, match="foo") as excinfo:
        RuntimeConfigPatch(core_groups=["foo"])

    assert "continuity" in str(excinfo.value)
