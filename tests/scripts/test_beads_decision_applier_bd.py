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
    [("not json", 1), ('{"error": "connection refused"}', 1)],
)
def test_bd_tracker_treats_a_failed_unstructured_answer_as_unavailable(
    tmp_path: Path, stdout, rc
) -> None:
    tracker = applier_mod.BdTracker(str(_fake_bd(tmp_path, show_stdout=stdout, show_rc=rc)))
    with pytest.raises(applier_mod.TrackerUnavailable):
        tracker.show("bu-x")


@pytest.mark.parametrize("stdout", ["[]", "not json", '[{"id": "bu-other"}]', '{"id": "bu-x"}'])
def test_bd_tracker_treats_a_clean_but_wrong_answer_as_this_bead_unreadable(
    tmp_path: Path, stdout
) -> None:
    tracker = applier_mod.BdTracker(str(_fake_bd(tmp_path, show_stdout=stdout, show_rc=0)))
    with pytest.raises(applier_mod.BeadUnreadable):
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


def _fake_bd_probe(tmp_path: Path, show_stdout: str, probe_stdout: str, probe_rc: int) -> Path:
    script = tmp_path / "bd-probe"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"with open({str(tmp_path / 'calls')!r}, 'a') as calls:\n"
        "    calls.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "print('Warning: host dolt.internal unreachable', file=sys.stderr)\n"
        "if sys.argv[1] == 'show':\n"
        f"    print({show_stdout!r})\n"
        "    sys.exit(1)\n"
        f"print({probe_stdout!r})\n"
        f"sys.exit({probe_rc})\n"
    )
    script.chmod(0o755)
    return script


@pytest.mark.parametrize("show_stdout", ["not json", '{"error": "broken bead"}'])
def test_failed_show_with_healthy_probe_is_bead_unreadable(tmp_path: Path, show_stdout) -> None:
    """REQ-owner-decision-desk-002: a healthy tracker isolates a failed show."""
    summary = {"total_issues": 2, "open_issues": 1, "in_progress_issues": 0, "closed_issues": 1}
    binary = _fake_bd_probe(tmp_path, show_stdout, json.dumps({"summary": summary}), 0)

    with pytest.raises(applier_mod.BeadUnreadable) as error:
        applier_mod.BdTracker(str(binary)).show("bu-x")

    assert str(error.value) == ""
    assert (tmp_path / "calls").read_text().splitlines() == [
        "show bu-x --json",
        "stats --json --no-activity",
    ]


@pytest.mark.parametrize(
    ("probe_stdout", "probe_rc"),
    [
        ("not json", 0),
        ("{}", 0),
        (
            '{"summary":{"total_issues":true,"open_issues":0,"in_progress_issues":0,"closed_issues":0}}',
            0,
        ),
        (
            '{"summary":{"total_issues":1,"open_issues":1,"in_progress_issues":0,"closed_issues":0}}',
            1,
        ),
    ],
)
def test_failed_show_requires_a_valid_successful_health_probe(
    tmp_path: Path, probe_stdout, probe_rc
) -> None:
    binary = _fake_bd_probe(tmp_path, '{"error":"broken bead"}', probe_stdout, probe_rc)

    with pytest.raises(applier_mod.TrackerUnavailable) as error:
        applier_mod.BdTracker(str(binary)).show("bu-x")

    assert str(error.value) == ""
    assert (tmp_path / "calls").read_text().splitlines() == [
        "show bu-x --json",
        "stats --json --no-activity",
    ]
