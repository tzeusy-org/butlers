"""Tests for scripts/check_archived_requirements_landed.py.

Regression guard for bu-966by. Nothing in this repo ever asserted that an
archived change's requirements reached ``openspec/specs/``, and 498 of them had
not -- for months, across 42 changes, while every check went green.
``openspec validate --changes --strict`` passes on a change whose entire
requirement set is missing from the baseline (it validates delta syntax, not
application) and ``openspec archive`` reports what it wrote, never what it
skipped. In the case that surfaced this, the archive was a hand-run ``git mv``
and the tool was never invoked at all, so there was no signal to be wrong.

The load-bearing case here is ``test_half_applied_archive_...``: a guard that
only notices a *fully* unapplied change is the same shape of defect as the ones
it is meant to catch -- a check credited with an answer it was never positioned
to give.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_archived_requirements_landed.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_archived_requirements_landed as guard  # noqa: E402

pytestmark = pytest.mark.unit


def _digest(requirement: str) -> str:
    """The digest a frozen entry pins for a missing requirement header."""
    return guard.digest_of(requirement)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _requirement(name: str, token: str) -> str:
    return (
        f"### Requirement: {name}\n"
        f"The system SHALL expose {token} on every request.\n\n"
        f"#### Scenario: {name} is served\n"
        f"- **WHEN** a caller reads {token}\n"
        f"- **THEN** the response carries {token}\n"
    )


def _tree(
    root: Path,
    *,
    baseline: dict[str, list[str]],
    archived: dict[str, dict[str, list[str]]],
    section: str = "## ADDED Requirements",
) -> Path:
    """Write a minimal openspec tree.

    ``baseline`` is ``{capability: [requirement name]}`` for ``openspec/specs``;
    ``archived`` is ``{change: {capability: [requirement name]}}`` for
    ``openspec/changes/archive``. Requirement bodies are generated from the name
    so a name present on both sides is byte-identical.
    """
    for capability, names in baseline.items():
        spec = root / "openspec" / "specs" / capability / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(_requirement(name, name.lower().replace(" ", "_")) for name in names)
        spec.write_text(f"# {capability}\n\n## Requirements\n\n{body}", encoding="utf-8")

    for change, capabilities in archived.items():
        for capability, names in capabilities.items():
            delta = (
                root
                / "openspec"
                / "changes"
                / "archive"
                / change
                / "specs"
                / capability
                / "spec.md"
            )
            delta.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(_requirement(name, name.lower().replace(" ", "_")) for name in names)
            delta.write_text(f"{section}\n\n{body}", encoding="utf-8")

    (root / "openspec" / "changes").mkdir(parents=True, exist_ok=True)
    return root


def _frozen(root: Path, entries: dict[str, list[dict[str, str | None]]]) -> Path:
    path = root / "frozen.json"
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return path


def _empty_frozen(root: Path) -> Path:
    return _frozen(root, {})


def test_added_requirements_absent_from_the_baseline_are_each_named(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={"widgets": []},
        archived={
            "2026-01-01-add-widgets": {"widgets": ["Widget API", "Widget audit", "Widget TTL"]}
        },
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, result.stdout
    for name in ("Widget API", "Widget audit", "Widget TTL"):
        assert name in result.stdout, f"{name!r} not named individually:\n{result.stdout}"


def test_added_requirements_present_in_the_baseline_pass(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={"widgets": ["Widget API", "Widget audit"]},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API", "Widget audit"]}},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 0, result.stdout


def test_half_applied_archive_fails_naming_only_the_requirements_that_did_not_land(
    tmp_path: Path,
) -> None:
    """The case a change-level check cannot see.

    Two of four requirements reached the baseline. Anything that asks "did this
    change write anything?" -- a spec file existing, a non-zero archive total --
    reports success here. The guard has to answer per requirement, and has to
    name the two that are missing without naming the two that landed.
    """
    _tree(
        tmp_path,
        baseline={"widgets": ["Widget API", "Widget audit"]},
        archived={
            "2026-01-01-add-widgets": {
                "widgets": ["Widget API", "Widget audit", "Widget TTL", "Widget replay"]
            }
        },
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, f"half-applied archive reported clean:\n{result.stdout}"
    assert "Widget TTL" in result.stdout
    assert "Widget replay" in result.stdout
    assert "Widget API" not in result.stdout, "named a requirement that did land"
    assert "Widget audit" not in result.stdout, "named a requirement that did land"


def test_frozen_entry_suppresses_a_known_missing_requirement(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={"widgets": []},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API"]}},
    )
    unfrozen = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))
    assert unfrozen.returncode == 1

    frozen = _frozen(
        tmp_path,
        {
            "2026-01-01-add-widgets/widgets/Widget API": [
                {
                    "kind": "requirement",
                    "scenario": None,
                    "digest": _digest("Widget API"),
                    "excerpt": "Widget API",
                }
            ]
        },
    )
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 0, result.stdout


def test_new_missing_requirement_fails_even_when_the_frozen_set_is_fully_populated(
    tmp_path: Path,
) -> None:
    """The ratchet must not swallow drift that arrives after the freeze.

    ``Widget API`` is frozen debt under bu-tk618. ``Widget replay`` is new. A
    ratchet keyed on anything coarser than the individual requirement -- a count,
    a per-change flag -- goes green here, which would make the freeze a mute
    button rather than a floor.
    """
    _tree(
        tmp_path,
        baseline={"widgets": []},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API", "Widget replay"]}},
    )
    frozen = _frozen(
        tmp_path,
        {
            "2026-01-01-add-widgets/widgets/Widget API": [
                {
                    "kind": "requirement",
                    "scenario": None,
                    "digest": _digest("Widget API"),
                    "excerpt": "Widget API",
                }
            ]
        },
    )
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 1, f"new drift swallowed by the ratchet:\n{result.stdout}"
    assert "Widget replay" in result.stdout
    assert "Widget API" not in result.stdout, "re-reported an entry that is frozen"


def test_missing_capability_spec_file_names_every_requirement(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API", "Widget audit"]}},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, result.stdout
    assert "Widget API" in result.stdout
    assert "Widget audit" in result.stdout


def test_modified_block_content_absent_from_the_baseline_is_reported(tmp_path: Path) -> None:
    root = _tree(tmp_path, baseline={"widgets": ["Widget API"]}, archived={})
    delta = (
        root
        / "openspec"
        / "changes"
        / "archive"
        / "2026-01-02-extend-widgets"
        / "specs"
        / "widgets"
        / "spec.md"
    )
    delta.parent.mkdir(parents=True, exist_ok=True)
    delta.write_text(
        "## MODIFIED Requirements\n\n"
        "### Requirement: Widget API\n"
        "The system SHALL expose widget api on every request.\n\n"
        "#### Scenario: Widget API is served\n"
        "- **WHEN** a caller reads widget api\n"
        "- **THEN** the response carries widget api\n"
        "- **AND** the response carries the widget cursor token\n",
        encoding="utf-8",
    )
    result = _run("--root", str(root), "--baseline", str(_empty_frozen(root)))

    assert result.returncode == 1, f"unapplied MODIFIED clause not reported:\n{result.stdout}"
    assert "cursor token" in result.stdout


def test_requirement_renamed_by_another_change_counts_as_landed(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        baseline={"widgets": ["Widget ledger"]},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API"]}},
    )
    delta = (
        root
        / "openspec"
        / "changes"
        / "archive"
        / "2026-02-01-rename-widgets"
        / "specs"
        / "widgets"
        / "spec.md"
    )
    delta.parent.mkdir(parents=True, exist_ok=True)
    delta.write_text(
        "## RENAMED Requirements\n\n"
        "- FROM: `### Requirement: Widget API`\n"
        "- TO: `### Requirement: Widget ledger`\n",
        encoding="utf-8",
    )
    result = _run("--root", str(root), "--baseline", str(_empty_frozen(root)))

    assert result.returncode == 0, result.stdout


def _retirement(root: Path, change: str, *, archived: bool) -> None:
    """Write a ``## REMOVED Requirements`` block retiring ``Widget API``."""
    parent = root / "openspec" / "changes"
    if archived:
        parent = parent / "archive"
    delta = parent / change / "specs" / "widgets" / "spec.md"
    delta.parent.mkdir(parents=True, exist_ok=True)
    delta.write_text(
        "## REMOVED Requirements\n\n### Requirement: Widget API\n\nRetired.\n",
        encoding="utf-8",
    )


def test_removed_block_in_an_archived_change_excuses_a_missing_requirement(
    tmp_path: Path,
) -> None:
    """The legitimate half. A requirement retired by a change that archived is gone on purpose."""
    root = _tree(
        tmp_path,
        baseline={"widgets": []},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API"]}},
    )
    _retirement(root, "2026-02-01-retire-widgets", archived=True)
    result = _run("--root", str(root), "--baseline", str(_empty_frozen(root)))

    assert result.returncode == 0, result.stdout


def test_removed_block_in_an_unarchived_change_does_not_excuse_a_missing_requirement(
    tmp_path: Path,
) -> None:
    """A pending proposal must not be able to mute a real gap.

    Unlike a rename, a removal is an unconditional skip -- nothing is looked up
    afterwards. Honouring one from a change that has not archived (and may never)
    would silence the finding permanently, with no ratchet entry and no JSON diff
    for a reviewer to see: the guard's own hatch, opened from outside the gate.
    """
    root = _tree(
        tmp_path,
        baseline={"widgets": []},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API"]}},
    )
    _retirement(root, "pending-retire-widgets", archived=False)
    result = _run("--root", str(root), "--baseline", str(_empty_frozen(root)))

    assert result.returncode == 1, f"a pending REMOVED block silenced a real gap:\n{result.stdout}"
    assert "Widget API" in result.stdout


def test_rename_in_an_unarchived_change_still_counts_as_landed(tmp_path: Path) -> None:
    """The asymmetry is deliberate and must survive someone tidying it into symmetry.

    Renames stay tree-wide because they cannot silence anything on their own:
    ``resolve_name`` only redirects the baseline lookup, so an absent new name
    still fires. Narrowing them to archived changes the way removals are narrowed
    would cost findings for no safety.
    """
    root = _tree(
        tmp_path,
        baseline={"widgets": ["Widget ledger"]},
        archived={"2026-01-01-add-widgets": {"widgets": ["Widget API"]}},
    )
    delta = (
        root / "openspec" / "changes" / "pending-rename-widgets" / "specs" / "widgets" / "spec.md"
    )
    delta.parent.mkdir(parents=True, exist_ok=True)
    delta.write_text(
        "## RENAMED Requirements\n\n"
        "- FROM: `### Requirement: Widget API`\n"
        "- TO: `### Requirement: Widget ledger`\n",
        encoding="utf-8",
    )
    result = _run("--root", str(root), "--baseline", str(_empty_frozen(root)))

    assert result.returncode == 0, result.stdout


def test_repo_scan_is_green_under_the_shipped_frozen_baseline() -> None:
    """CI on main must be green: the freeze covers every pre-existing gap."""
    result = _run()

    assert result.returncode == 0, result.stdout[-4000:]
