"""Tests for scripts/check_requirement_trace_lines.py (bu-xjs98p.1).

New and edited requirements in active OpenSpec changes must carry identity: an ID whose
capability matches the spec directory, a Source and a Scope.  Pre-existing debt is frozen in a
content-addressed baseline, so editing a frozen block makes it carry the lines.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_requirement_trace_lines.py"

pytestmark = pytest.mark.unit


def _rid(capability: str, number: int) -> str:
    """Built at runtime so the cited-requirements guard does not read these as citations."""
    return "-".join(["REQ", capability, f"{number:03d}"])


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--baseline",
            str(root / "baseline.json"),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _delta(root: Path, change: str, capability: str, body: str) -> Path:
    path = root / "openspec" / "changes" / change / "specs" / capability / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _block(name: str, *, req_id: str | None, source: bool = True, scope: bool = True) -> str:
    lines = [f"### Requirement: {name}", "The system SHALL do the thing.", ""]
    if req_id:
        lines.append(f"ID: {req_id}")
    if source:
        lines.append("Source: RFC 0001")
    if scope:
        lines.append("Scope: v1-mandatory")
    lines += ["", "#### Scenario: It works", "- **WHEN** x", "- **THEN** y", ""]
    return "\n".join(lines)


def _doc(section: str, *blocks: str) -> str:
    return f"## {section} Requirements\n\n" + "\n".join(blocks)


def test_a_block_with_id_source_and_scope_passes_and_a_missing_line_names_the_field(
    tmp_path: Path,
) -> None:
    _delta(tmp_path, "good", "widgets", _doc("ADDED", _block("Ok", req_id=_rid("widgets", 1))))
    assert _run(tmp_path).returncode == 0

    path = _delta(
        tmp_path,
        "bad",
        "widgets",
        _doc("ADDED", _block("Bare", req_id=None, source=False, scope=False)),
    )
    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"{path.relative_to(tmp_path)}:3" in result.stdout
    for field in ("missing ID", "missing Source", "missing Scope"):
        assert field in result.stdout


def test_editing_a_frozen_block_makes_it_fail_until_it_carries_the_lines(tmp_path: Path) -> None:
    legacy = _doc("MODIFIED", _block("Legacy", req_id=None, source=False, scope=False))
    path = _delta(tmp_path, "old", "widgets", legacy)
    assert _run(tmp_path, "--write-baseline").returncode == 0
    assert _run(tmp_path).returncode == 0  # frozen debt passes

    path.write_text(legacy.replace("SHALL do the thing", "SHALL do another thing"), "utf-8")
    edited = _run(tmp_path)
    assert edited.returncode == 1
    assert "missing ID" in edited.stdout

    path.write_text(_doc("MODIFIED", _block("Legacy", req_id=_rid("widgets", 2))), "utf-8")
    assert _run(tmp_path).returncode == 0


def test_a_wrong_capability_id_fails(tmp_path: Path) -> None:
    _delta(tmp_path, "c", "widgets", _doc("ADDED", _block("Wrong", req_id=_rid("gadgets", 1))))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "names capability 'gadgets'" in result.stdout


def test_a_new_id_minted_twice_in_one_capability_fails_but_a_modified_reuse_passes(
    tmp_path: Path,
) -> None:
    _delta(tmp_path, "a", "widgets", _doc("ADDED", _block("One", req_id=_rid("widgets", 1))))
    _delta(tmp_path, "b", "widgets", _doc("ADDED", _block("Two", req_id=_rid("widgets", 1))))
    _delta(tmp_path, "c", "gadgets", _doc("ADDED", _block("Same", req_id=_rid("gadgets", 1))))

    dup = _run(tmp_path)
    assert dup.returncode == 1
    assert f"duplicate new ID {_rid('widgets', 1)} (also minted by b)" in dup.stdout
    assert "gadgets" not in dup.stdout  # same number in another capability is fine

    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("Two", req_id=_rid("widgets", 1))))
    assert _run(tmp_path).returncode == 0


def test_removed_renamed_sections_and_the_archive_are_ignored(tmp_path: Path) -> None:
    bare = _block("Bare", req_id=None, source=False, scope=False)
    _delta(tmp_path, "r", "widgets", _doc("REMOVED", bare) + "\n" + _doc("RENAMED", bare))
    _delta(tmp_path, "archive/old", "widgets", _doc("ADDED", bare))
    _delta(tmp_path, "ok", "widgets", _doc("ADDED", _block("Ok", req_id=_rid("widgets", 1))))

    assert _run(tmp_path).returncode == 0


def test_write_baseline_is_idempotent_and_an_empty_scan_fails(tmp_path: Path) -> None:
    empty = _run(tmp_path)
    assert empty.returncode == 2
    assert "empty scan" in empty.stderr

    _delta(
        tmp_path,
        "old",
        "widgets",
        _doc("ADDED", _block("Zed", req_id=None), _block("Alpha", req_id=None)),
    )
    assert _run(tmp_path, "--write-baseline").returncode == 0
    first = (tmp_path / "baseline.json").read_bytes()
    assert _run(tmp_path, "--write-baseline").returncode == 0

    assert (tmp_path / "baseline.json").read_bytes() == first
    assert list(json.loads(first)) == sorted(json.loads(first))
