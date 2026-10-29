"""A butler's name must equal its roster directory name, or retirement fails closed [bu-b2s1tu].

The retirement signal keys on ``roster/<dir>/`` while legacy liveness episodes key on
``butler.name``. A mismatch could make a live butler read as retired or block a real
retirement, so discovery records it as failed and no butler reads as retired.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from butlers.api.deps import discover_butlers_with_diagnostics

pytestmark = pytest.mark.unit

_ROSTER = Path(__file__).resolve().parents[2] / "roster"

_TOML = """\
[butler]
name = "{name}"
port = {port}
description = "Test butler"

[butler.db]
name = "butlers"
schema = "{schema}"
"""


def _butler(roster: Path, directory: str, name: str, port: int) -> None:
    (roster / directory).mkdir(parents=True)
    (roster / directory / "butler.toml").write_text(_TOML.format(name=name, port=port, schema=name))


def test_mismatched_name_is_failed_and_disables_retirement_for_everyone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _butler(tmp_path, "alpha", "alpha", 41001)
    _butler(tmp_path, "beta", "gamma", 41002)

    with caplog.at_level(logging.WARNING, logger="butlers.api.deps"):
        butlers, diag = discover_butlers_with_diagnostics(tmp_path)

    assert diag.failed == frozenset({"beta"})
    assert not diag.clean
    assert diag.is_retired("gone") is False
    assert diag.is_retired("alpha") is False
    assert diag.is_retired("gamma") is False
    assert "does not match its roster directory" in caplog.text
    assert sorted(b.name for b in butlers) == ["alpha", "gamma"]


def test_matching_names_stay_clean_and_still_detect_retirement(tmp_path: Path) -> None:
    _butler(tmp_path, "alpha", "alpha", 41001)
    _butler(tmp_path, "beta", "beta", 41002)

    _, diag = discover_butlers_with_diagnostics(tmp_path)

    assert diag.clean
    assert diag.is_retired("gone") is True
    assert diag.is_retired("beta") is False


def test_real_roster_has_no_name_mismatch() -> None:
    _, diag = discover_butlers_with_diagnostics(_ROSTER)

    assert diag.roster_found
    assert diag.failed == frozenset()
