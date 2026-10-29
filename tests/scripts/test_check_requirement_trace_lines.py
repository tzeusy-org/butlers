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
    baseline = tmp_path / "baseline.json"
    frozen = baseline.read_bytes()

    path.write_text(legacy.replace("SHALL do the thing", "SHALL do another thing"), "utf-8")
    edited = _run(tmp_path)
    assert edited.returncode == 1
    assert "missing ID" in edited.stdout

    path.write_text(_doc("MODIFIED", _block("Legacy", req_id=_rid("widgets", 2))), "utf-8")
    identified = _run(tmp_path)
    assert identified.returncode == 1
    assert "stale baseline" in identified.stdout
    assert "old/widgets/Legacy" in identified.stdout
    assert _run(tmp_path, "--strict").returncode == 0  # strict ignores the stale exemption
    baseline.write_text("{}\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0

    # An archived/removed block is stale too; retain an identified active spec
    # so this tests stale refusal rather than the separate empty-scan boundary.
    baseline.write_bytes(frozen)
    path.unlink()
    _delta(
        tmp_path, "current", "widgets", _doc("ADDED", _block("Current", req_id=_rid("widgets", 3)))
    )
    gone = _run(tmp_path)
    assert gone.returncode == 1
    assert "stale baseline" in gone.stdout
    assert "old/widgets/Legacy" in gone.stdout
    baseline.write_text("{}\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0


def test_a_wrong_capability_id_fails(tmp_path: Path) -> None:
    _delta(tmp_path, "c", "widgets", _doc("ADDED", _block("Wrong", req_id=_rid("gadgets", 1))))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "names capability 'gadgets'" in result.stdout


def test_a_new_id_minted_twice_in_one_capability_fails_even_when_a_modified_block_mints_it(
    tmp_path: Path,
) -> None:
    _delta(tmp_path, "a", "widgets", _doc("ADDED", _block("One", req_id=_rid("widgets", 1))))
    _delta(tmp_path, "b", "widgets", _doc("ADDED", _block("Two", req_id=_rid("widgets", 1))))
    _delta(tmp_path, "c", "gadgets", _doc("ADDED", _block("Same", req_id=_rid("gadgets", 1))))

    dup = _run(tmp_path)
    assert dup.returncode == 1
    assert f"duplicate new ID {_rid('widgets', 1)} (also minted by b)" in dup.stdout
    assert "gadgets" not in dup.stdout  # same number in another capability is fine

    # A MODIFIED block minting an ID another change already minted for a different requirement
    # is the same collision.
    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("Two", req_id=_rid("widgets", 1))))
    assert _run(tmp_path).returncode == 1


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


def _canonical(root: Path, capability: str, body: str) -> None:
    path = root / "openspec" / "specs" / capability / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_an_added_id_that_the_canonical_spec_already_has_fails(tmp_path: Path) -> None:
    rid = _rid("widgets", 1)
    _canonical(tmp_path, "widgets", "## Requirements\n\n" + _block("Existing", req_id=rid))
    _delta(tmp_path, "c", "widgets", _doc("ADDED", _block("Fresh", req_id=rid)))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"ID {rid} already exists in openspec/specs/widgets" in result.stdout


def test_a_modified_block_may_keep_its_canonical_id_but_not_take_another_requirements(
    tmp_path: Path,
) -> None:
    rid = _rid("widgets", 1)
    _canonical(tmp_path, "widgets", "## Requirements\n\n" + _block("Existing", req_id=rid))
    _delta(tmp_path, "keep", "widgets", _doc("MODIFIED", _block("Existing", req_id=rid)))
    assert _run(tmp_path).returncode == 0

    _delta(tmp_path, "keep", "widgets", _doc("MODIFIED", _block("Other", req_id=rid)))
    result = _run(tmp_path)

    assert result.returncode == 1
    assert "belongs to requirement 'Existing'" in result.stdout


def test_two_changes_modifying_different_requirements_with_one_new_id_collide(
    tmp_path: Path,
) -> None:
    rid = _rid("widgets", 7)
    _delta(tmp_path, "a", "widgets", _doc("MODIFIED", _block("One", req_id=rid)))
    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("Two", req_id=rid)))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"duplicate new ID {rid}" in result.stdout

    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("One", req_id=rid)))
    assert _run(tmp_path).returncode == 0  # the same requirement, agreed on by both


def test_each_trace_line_is_validated_on_its_own(tmp_path: Path) -> None:
    def block(*lines: str) -> str:
        return "\n".join(["### Requirement: R", "The system SHALL.", "", *lines, ""])

    cases = {
        "'widgets-1' is not REQ-": block("ID: widgets-1", "Source: x", "Scope: v1-mandatory"),
        "missing Source": block(f"ID: {_rid('widgets', 1)}", "Source:", "Scope: v1-mandatory"),
        "missing Scope": block(f"ID: {_rid('widgets', 1)}", "Source: x", "Scope: v2"),
    }
    for expected, body in cases.items():
        _delta(tmp_path, "c", "widgets", "## ADDED Requirements\n\n" + body)
        result = _run(tmp_path)
        assert result.returncode == 1, expected
        assert expected in result.stdout, expected


def test_a_code_fence_hides_headers_and_trace_lines_from_the_gate(tmp_path: Path) -> None:
    fenced_id = "\n".join(
        [
            "### Requirement: Fenced lines",
            "The system SHALL.",
            "```",
            f"ID: {_rid('widgets', 1)}",
            "Source: x",
            "Scope: v1-mandatory",
            "```",
            "",
        ]
    )
    _delta(tmp_path, "a", "widgets", "## ADDED Requirements\n\n" + fenced_id)
    lines_in_fence = _run(tmp_path)
    assert lines_in_fence.returncode == 1  # lines inside a fence do not satisfy the block
    assert "missing ID" in lines_in_fence.stdout

    fenced_header = "\n".join(
        [
            "## ADDED Requirements",
            "",
            _block("Real", req_id=_rid("widgets", 2)),
            "```markdown",
            "### Requirement: Only an example",
            "```",
            "",
        ]
    )
    _delta(tmp_path, "a", "widgets", fenced_header)
    assert _run(tmp_path).returncode == 0  # a header inside a fence is not a block


def test_parser_parity_with_openspec_headers_fences_and_stray_headings(tmp_path: Path) -> None:
    # Case-insensitive header, and no space needed after the hashes.
    for header in ("### requirement: Sneaky", "###Requirement: Glued"):
        _delta(tmp_path, "a", "widgets", f"## ADDED Requirements\n\n{header}\nThe system SHALL.\n")
        result = _run(tmp_path)
        assert result.returncode == 1, header
        assert "missing ID" in result.stdout, header

    # A lone ``` line inside a ~~~ fence does not close it, so the next header stays hidden
    # exactly as OpenSpec hides it; a bare ~~~ closer ends it and the header counts.
    still_open = "\n".join(
        ["## ADDED Requirements", "", "~~~markdown", "```", "### Requirement: Hidden", "~~~", ""]
    )
    _delta(tmp_path, "a", "widgets", still_open)
    assert _run(tmp_path).returncode == 0
    closed_then_header = still_open.replace("~~~\n", "~~~\n~~~\n", 1).replace(
        "### Requirement: Hidden\n~~~\n~~~", "~~~\n### Requirement: Visible"
    )
    _delta(tmp_path, "a", "widgets", closed_then_header)
    assert _run(tmp_path).returncode == 1

    # A stray ### heading stays inside the block (archive keeps it), so its lines are digested.
    body = _block("Legacy", req_id=None, source=False, scope=False)
    frozen = _doc("ADDED", body + "\n### Source References\n- old\n")
    path = _delta(tmp_path, "a", "widgets", frozen)
    assert _run(tmp_path, "--write-baseline").returncode == 0
    assert _run(tmp_path).returncode == 0
    path.write_text(frozen.replace("- old", "- new"), encoding="utf-8")
    assert _run(tmp_path).returncode == 1


def test_moving_a_frozen_block_between_sections_unfreezes_it(tmp_path: Path) -> None:
    bare = _block("Legacy", req_id=None, source=False, scope=False)
    path = _delta(tmp_path, "a", "widgets", _doc("ADDED", bare))
    assert _run(tmp_path, "--write-baseline").returncode == 0
    assert _run(tmp_path).returncode == 0

    path.write_text(_doc("MODIFIED", bare), encoding="utf-8")

    assert _run(tmp_path).returncode == 1


def test_a_modified_block_must_carry_the_id_its_requirement_already_has(tmp_path: Path) -> None:
    held, fresh = _rid("widgets", 1), _rid("widgets", 5)
    _canonical(tmp_path, "widgets", "## Requirements\n\n" + _block("Existing", req_id=held))
    _delta(tmp_path, "c", "widgets", _doc("MODIFIED", _block("Existing", req_id=fresh)))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"requirement 'Existing' already has canonical ID {held}" in result.stdout

    _delta(tmp_path, "c", "widgets", _doc("MODIFIED", _block("Existing", req_id=held)))
    assert _run(tmp_path).returncode == 0


def test_a_modified_block_must_carry_the_id_another_change_adds_its_requirement_under(
    tmp_path: Path,
) -> None:
    added, other = _rid("widgets", 3), _rid("widgets", 9)
    _delta(tmp_path, "a", "widgets", _doc("ADDED", _block("Shared", req_id=added)))
    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("Shared", req_id=other)))

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"requirement 'Shared' is ADDED as {added} by a" in result.stdout

    _delta(tmp_path, "b", "widgets", _doc("MODIFIED", _block("Shared", req_id=added)))
    assert _run(tmp_path).returncode == 0


def test_two_added_blocks_with_one_requirement_name_and_id_collide_under_strict(
    tmp_path: Path,
) -> None:
    rid = _rid("widgets", 2)
    _delta(tmp_path, "a", "widgets", _doc("ADDED", _block("Same", req_id=rid)))
    _delta(tmp_path, "b", "widgets", _doc("ADDED", _block("Same", req_id=rid)))

    result = _run(tmp_path, "--strict")

    assert result.returncode == 1
    assert f"duplicate new ID {rid}" in result.stdout
