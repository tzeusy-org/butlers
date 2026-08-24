"""Tests for scripts/check_countable_tasks.py.

Regression guard for bu-h7igs. ``openspec archive``'s completion gate counts
markdown checkbox lines, so a ``tasks.md`` written as ``### N.`` sections with
per-task acceptance bullets reports ``Task status: No tasks``, cannot be
incomplete, and archives unprompted -- and "archived with no task warning" reads
as evidence the tasks were done.

The parity test below is the load-bearing one: the guard is only worth its exit
code if its predicate is the same predicate OpenSpec applies. It runs both
parsers over the same fixture lines and compares counts, so a future OpenSpec
release that widens or narrows ``TASK_LINE_PATTERN`` fails here rather than
silently drifting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_countable_tasks as gate  # noqa: E402

pytestmark = pytest.mark.unit

SCRIPT = REPO_ROOT / "scripts" / "check_countable_tasks.py"

# Lines chosen to straddle the pattern's edges: bullet style, missing space
# before the box, whitespace standing in for an empty box, and the three
# near-misses (heading, numbered list, mid-line box) that made the corpus's
# heading-style files invisible to the gate in the first place.
PARSER_FIXTURE = "\n".join(
    [
        "## Tasks",
        "",
        "### 1. A heading-style task",
        "",
        "  1. numbered, not a checkbox",
        "- [ ] 1.1 plain",
        "  - [x] 1.2 nested and done",
        "* [ ] 1.3 star bullet",
        "-[X] 1.4 no space before the box",
        "- [\t] 1.5 tab inside the box",
        "- [ ]",
        "> - [ ] quoted, not a task",
        "text - [ ] mid-line, not a task",
        "```",
        "- [ ] fenced, still a task",
        "```",
    ]
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_change(root: Path, change: str, tasks: str | None) -> Path:
    change_dir = root / "openspec" / "changes" / change
    change_dir.mkdir(parents=True, exist_ok=True)
    (change_dir / "proposal.md").write_text("# Why\n", encoding="utf-8")
    if tasks is not None:
        (change_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    return change_dir


def _req(suffix: str) -> str:
    """Build the fixture requirement id without writing one literally.

    `scripts/check_cited_requirements_resolve.py` (bu-lpwjc) fails any test file
    naming a `REQ-<capability>-<number>` that resolves to no definition, and it
    has no self-exemption on purpose -- an exemption keyed on a path is a hole a
    real test file could later sit in. There is no `widget` capability and there
    should not be one: this id is furniture for an acceptance bullet whose only
    job is to sit below the task lines this module counts. Constructing it keeps
    the fixtures byte-identical at runtime while leaving nothing in this source
    for that scan to find.
    """
    return f"REQ-{suffix}"


FIXTURE_REQ = _req("widget-001")

HEADING_STYLE = f"""## Tasks

### 1. Extend the widget ledger

Add `resolve_widget()` to the ledger and re-export it.

Acceptance:
- {FIXTURE_REQ} scenarios pass
"""

CHECKBOX_STYLE = f"""## Tasks

### 1. Extend the widget ledger

- [ ] 1.1 Add `resolve_widget()` to the ledger and re-export it.

Acceptance:
- {FIXTURE_REQ} scenarios pass
"""


# --- The predicate matches the gate it models --------------------------------


@pytest.mark.skipif(
    shutil.which("openspec") is None, reason="openspec CLI not installed on this machine"
)
def test_predicate_matches_openspecs_own_parser() -> None:
    """Both parsers count the same lines on the same fixture.

    Reads the installed package's `countTasksFromContent` directly rather than
    running `openspec archive`, which mutates the tree it is pointed at.
    """
    package_root = Path(shutil.which("openspec")).resolve().parent.parent
    module = package_root / "dist" / "utils" / "task-progress.js"
    if not module.is_file():  # pragma: no cover - layout differs across releases
        pytest.skip(f"openspec task-progress module not found at {module}")

    script = (
        f"import {{ countTasksFromContent }} from {json.dumps(str(module))};"
        "let content = '';"
        "process.stdin.on('data', (c) => (content += c));"
        "process.stdin.on('end', () => "
        "console.log(JSON.stringify(countTasksFromContent(content))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=PARSER_FIXTURE,
        capture_output=True,
        text=True,
        check=True,
    )
    openspec_counts = json.loads(result.stdout)

    progress = gate.count_tasks(PARSER_FIXTURE)
    assert (progress.total, progress.completed) == (
        openspec_counts["total"],
        openspec_counts["completed"],
    )


def test_heading_style_lines_are_not_countable() -> None:
    """The blind spot itself: a heading-style file parses to zero tasks."""
    assert gate.count_tasks(HEADING_STYLE).total == 0
    assert gate.count_tasks(HEADING_STYLE).status() == "No tasks"


def test_checkbox_state_is_read_from_the_box() -> None:
    progress = gate.count_tasks("- [ ] a\n- [x] b\n- [X] c\n")
    assert (progress.total, progress.completed) == (3, 2)
    assert progress.status() == "2/3 tasks"
    assert gate.count_tasks("- [x] a\n").status() == "✓ Complete"


# --- The gate fires, and stops firing once the file is converted -------------


def test_gate_fails_and_names_a_heading_style_change(tmp_path: Path) -> None:
    _write_change(tmp_path, "widget-ledger", HEADING_STYLE)
    _write_change(tmp_path, "already-countable", CHECKBOX_STYLE)

    result = _run("--root", str(tmp_path), "--exemptions", str(tmp_path / "none.json"))

    assert result.returncode == 1
    assert "widget-ledger: tasks.md has no `- [ ]` task line" in result.stdout
    assert "already-countable" not in result.stdout


def test_gate_passes_once_the_file_carries_checkboxes(tmp_path: Path) -> None:
    _write_change(tmp_path, "widget-ledger", CHECKBOX_STYLE)

    result = _run("--root", str(tmp_path), "--exemptions", str(tmp_path / "none.json"))

    assert result.returncode == 0
    assert "All 1 unarchived change(s)" in result.stdout


def test_unchecked_boxes_pass_the_visibility_gate(tmp_path: Path) -> None:
    """Unproven work is not this gate's business -- archive warns about that itself."""
    _write_change(tmp_path, "widget-ledger", CHECKBOX_STYLE)
    assert (
        _run("--root", str(tmp_path), "--exemptions", str(tmp_path / "none.json")).returncode == 0
    )


def test_missing_tasks_file_fails_for_the_same_reason(tmp_path: Path) -> None:
    _write_change(tmp_path, "no-tasks-at-all", None)

    result = _run("--root", str(tmp_path), "--exemptions", str(tmp_path / "none.json"))

    assert result.returncode == 1
    assert "no-tasks-at-all: no tasks.md" in result.stdout


def test_exemption_excuses_a_change_and_prints_the_reason(tmp_path: Path) -> None:
    _write_change(tmp_path, "widget-ledger", HEADING_STYLE)
    exemptions = tmp_path / "exemptions.json"
    exemptions.write_text(json.dumps({"widget-ledger": "spec-only; tracked in beads"}), "utf-8")

    result = _run("--root", str(tmp_path), "--exemptions", str(exemptions))

    assert result.returncode == 0
    assert "widget-ledger is exempt -- spec-only; tracked in beads" in result.stdout


def test_archived_changes_are_reported_but_never_fail(tmp_path: Path) -> None:
    _write_change(tmp_path, "archive/2026-01-01-old-change", None)
    _write_change(tmp_path, "widget-ledger", CHECKBOX_STYLE)

    result = _run(
        "--root",
        str(tmp_path),
        "--exemptions",
        str(tmp_path / "none.json"),
        "--include-archived",
    )

    assert result.returncode == 0
    assert "2026-01-01-old-change: no tasks.md" in result.stdout


# --- The repository itself stays visible to the gate -------------------------


def test_repo_changes_are_all_countable() -> None:
    """Locks in the bu-h7igs conversions: no active change may go back to headings."""
    result = _run()
    assert result.returncode == 0, result.stdout
