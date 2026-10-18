"""Roster discovery diagnostics behind the retired-butler signal. [bu-h1b5un]"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.api.deps import (
    DiscoveryDiagnostics,
    discover_butlers,
    discover_butlers_with_diagnostics,
)

pytestmark = pytest.mark.unit

_TOML = """\
[butler]
name = "{name}"
port = {port}
description = "Test butler"

[butler.db]
name = "butlers"
schema = "{name}"
"""


def _butler(roster: Path, name: str, port: int) -> None:
    (roster / name).mkdir(parents=True)
    (roster / name / "butler.toml").write_text(_TOML.format(name=name, port=port))


def test_clean_pass_marks_only_absent_directories_retired(tmp_path: Path) -> None:
    _butler(tmp_path, "alpha", 41001)
    butlers, diag = discover_butlers_with_diagnostics(tmp_path)
    assert [b.name for b in butlers] == ["alpha"]
    assert diag.clean
    assert diag.is_retired("gone") is True
    assert diag.is_retired("alpha") is False


def test_directory_without_toml_is_present_but_not_a_global_failure(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    _butler(tmp_path, "alpha", 41001)
    _, diag = discover_butlers_with_diagnostics(tmp_path)
    assert diag.clean
    assert diag.is_retired("shared") is False


def test_unparsable_config_blocks_retirement_for_every_butler(tmp_path: Path) -> None:
    _butler(tmp_path, "alpha", 41001)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "butler.toml").write_text("not = [valid")
    _, diag = discover_butlers_with_diagnostics(tmp_path)
    assert diag.failed == frozenset({"broken"})
    assert not diag.clean
    assert diag.is_retired("gone") is False


def test_missing_roster_is_never_a_retirement_signal(tmp_path: Path) -> None:
    butlers, diag = discover_butlers_with_diagnostics(tmp_path / "absent")
    assert butlers == []
    assert diag.is_retired("anything") is False


def test_default_diagnostics_and_discover_butlers_return_type(tmp_path: Path) -> None:
    assert DiscoveryDiagnostics().is_retired("x") is False
    _butler(tmp_path, "alpha", 41001)
    result = discover_butlers(tmp_path)
    assert isinstance(result, list)
    assert [b.name for b in result] == ["alpha"]
