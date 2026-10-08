"""``BdTracker``: the applier's only interface to ``bd`` (bu-ckkpz.3).

Unit-level, against a fake ``bd`` shell script: no tracker, no database.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import beads_decision_applier as applier_mod  # noqa: E402

pytestmark = pytest.mark.unit


def _fake_bd(tmp_path: Path, *, show_stdout: str, show_rc: int, close_rc: int = 0) -> Path:
    script = tmp_path / "bd"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path}/calls"\n'
        'if [ "$1" = show ]; then\n'
        f"  cat <<'JSON'\n{show_stdout}\nJSON\n"
        "  echo 'Warning: host dolt.internal unreachable' >&2\n"
        f"  exit {show_rc}\n"
        "fi\n"
        f"exit {close_rc}\n"
    )
    script.chmod(0o755)
    return script


def test_bd_tracker_parses_show_and_not_found(tmp_path: Path) -> None:
    issue = {"id": "bu-x", "status": "open"}
    found = applier_mod.BdTracker(
        str(_fake_bd(tmp_path, show_stdout=json.dumps([issue]), show_rc=0))
    )
    assert found.show("bu-x") == issue

    missing = applier_mod.BdTracker(
        str(_fake_bd(tmp_path, show_stdout='{"error": "no issues found matching"}', show_rc=1))
    )
    assert missing.show("bu-x") is None


@pytest.mark.parametrize(
    ("stdout", "rc"),
    [("not json", 1), ('{"error": "connection refused"}', 1), ("[]", 0)],
)
def test_bd_tracker_treats_anything_else_as_unavailable(tmp_path: Path, stdout, rc) -> None:
    tracker = applier_mod.BdTracker(str(_fake_bd(tmp_path, show_stdout=stdout, show_rc=rc)))
    with pytest.raises(applier_mod.TrackerUnavailable):
        tracker.show("bu-x")


def test_bd_tracker_close_passes_the_reason_as_one_argument(tmp_path: Path) -> None:
    bd = _fake_bd(tmp_path, show_stdout="[]", show_rc=0, close_rc=0)
    applier_mod.BdTracker(str(bd)).close("bu-x", "Decision: A; B (decision-intent 1, via x)")
    assert (tmp_path / "calls").read_text() == (
        "close bu-x --reason Decision: A; B (decision-intent 1, via x)\n"
    )

    failing = _fake_bd(tmp_path, show_stdout="[]", show_rc=0, close_rc=1)
    with pytest.raises(applier_mod.CloseFailed):
        applier_mod.BdTracker(str(failing)).close("bu-x", "r")


def test_missing_binary_is_unavailable() -> None:
    tracker = applier_mod.BdTracker("/nonexistent/bd")
    with pytest.raises(applier_mod.TrackerUnavailable):
        tracker.show("bu-x")
    with pytest.raises(applier_mod.CloseFailed):
        tracker.close("bu-x", "r")


def test_script_does_not_import_the_butlers_package() -> None:
    """The bridge image has asyncpg and this file, not ``butlers``."""
    source = (REPO_ROOT / "scripts" / "beads_decision_applier.py").read_text()
    assert "import butlers" not in source and "from butlers" not in source
