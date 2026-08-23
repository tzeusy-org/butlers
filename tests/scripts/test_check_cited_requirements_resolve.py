"""Tests for scripts/check_cited_requirements_resolve.py.

Regression guard for bu-lpwjc. 16 test docstrings cite
``REQ-database-security-006``, a requirement that exists only in
``openspec/changes/restore-drill-recovery-truthfulness/`` -- an unarchived
change. Nothing in this repo ever asserted that a requirement id cited by a
test resolves to a definition anywhere. The sibling guards read the spec tree
against itself (``check_spec_overwrites.py`` compares unarchived deltas to the
baseline; ``check_archived_requirements_landed.py`` compares archived deltas to
the baseline) and neither has ever opened a test file. So if that change is
archived with ``--skip-specs``, half-applied, or dropped, 16 tests would cite a
requirement with no canonical home and every check would stay green.

The load-bearing cases here are the two a naive predicate gets wrong:

* ``test_id_defined_only_by_an_archived_change_does_not_resolve`` -- an archived
  change is *not* a definition source. Reading ``openspec/changes/**`` as one
  flat source (the obvious reading, since archived changes do live under it)
  makes the guard blind to precisely the failure in the bead: archive the change
  with ``--skip-specs`` and the delta moves to ``archive/`` while the baseline
  gains nothing, yet the citation still "resolves" -- permanently.
* ``test_half_applied_archive_names_only_the_id_that_did_not_land`` -- a
  capability-level verdict ("does ``openspec/specs/widgets/spec.md`` exist?",
  "did that change write anything?") goes green when one of a change's two cited
  ids landed and the other did not. The guard has to answer per id.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_cited_requirements_resolve.py"

pytestmark = pytest.mark.unit


def _req(suffix: str) -> str:
    """Build a fixture requirement id without writing one literally.

    A fixture id written out in full here would be a dangling citation by the
    guard's own definition, and the guard has no self-exemption on purpose:
    an exemption keyed on a path is a hole a real test file could later sit in.
    Constructing the id keeps the fixtures exercising the true citation shape at
    runtime while leaving nothing for the scan to find in this source.
    """
    return f"REQ-{suffix}"


IN_BASELINE = _req("widgets-001")
IN_BASELINE_TOO = _req("widgets-002")
AUTHORED = _req("widgets-006")
AUTHORED_TOO = _req("widgets-007")
DANGLING = _req("widgets-404")
DANGLING_TOO = _req("widgets-405")
DANGLING_ELSEWHERE = _req("gadgets-406")

# Change-local shorthand. Safe to write literally: it names no capability, so it
# is not a citation the guard recognises -- which is what the test asserts.
SHORTHAND = "REQ-005"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _requirement(req_id: str) -> str:
    token = req_id.rsplit("-", 1)[0].removeprefix("REQ-")
    return (
        f"### Requirement: {req_id}\n"
        f"The system SHALL expose {token} on every request.\n\n"
        f"ID: {req_id}\n"
        f"Scope: v1-mandatory\n\n"
        f"#### Scenario: {token} is served\n"
        f"- **WHEN** a caller reads {token}\n"
        f"- **THEN** the response carries {token}\n"
    )


def _spec(path: Path, ids: list[str], *, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(_requirement(req_id) for req_id in ids)
    path.write_text(f"{header}\n\n{body}", encoding="utf-8")


def _tree(
    root: Path,
    *,
    baseline: dict[str, list[str]] | None = None,
    pending: dict[str, dict[str, list[str]]] | None = None,
    archived: dict[str, dict[str, list[str]]] | None = None,
    cited: dict[str, list[str]] | None = None,
) -> Path:
    """Write a minimal openspec tree plus the test files that cite into it.

    ``baseline`` is ``{capability: [requirement id]}`` for ``openspec/specs``;
    ``pending`` and ``archived`` are ``{change: {capability: [requirement id]}}``
    for ``openspec/changes`` and ``openspec/changes/archive``; ``cited`` is
    ``{test file path relative to root: [requirement id]}``.
    """
    for capability, ids in (baseline or {}).items():
        _spec(
            root / "openspec" / "specs" / capability / "spec.md",
            ids,
            header=f"# {capability}\n\n## Requirements",
        )

    for change, capabilities in (pending or {}).items():
        for capability, ids in capabilities.items():
            _spec(
                root / "openspec" / "changes" / change / "specs" / capability / "spec.md",
                ids,
                header="## ADDED Requirements",
            )

    for change, capabilities in (archived or {}).items():
        for capability, ids in capabilities.items():
            _spec(
                root
                / "openspec"
                / "changes"
                / "archive"
                / change
                / "specs"
                / capability
                / "spec.md",
                ids,
                header="## ADDED Requirements",
            )

    for relative, ids in (cited or {}).items():
        test_file = root / relative
        test_file.parent.mkdir(parents=True, exist_ok=True)
        stanzas = "\n\n".join(
            f"def test_widget_{index}() -> None:\n"
            f'    """{req_id}: the widget is served."""\n'
            f"    assert True\n"
            for index, req_id in enumerate(ids)
        )
        test_file.write_text(f'"""Widget tests."""\n\n\n{stanzas}', encoding="utf-8")

    (root / "openspec" / "changes").mkdir(parents=True, exist_ok=True)
    return root


def _frozen(root: Path, entries: dict[str, list[str]]) -> Path:
    path = root / "frozen.json"
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return path


def _empty_frozen(root: Path) -> Path:
    return _frozen(root, {})


def test_id_cited_with_no_definition_anywhere_fails_and_is_named(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={"widgets": [IN_BASELINE]},
        cited={"tests/test_widgets.py": [IN_BASELINE, DANGLING]},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, f"a dangling citation reported clean:\n{result.stdout}"
    assert DANGLING in result.stdout
    assert IN_BASELINE not in result.stdout, "named an id that does resolve"
    assert "tests/test_widgets.py" in result.stdout, "did not say where the citation lives"


def test_every_unresolved_citation_is_named_individually(tmp_path: Path) -> None:
    """Never an aggregate count. Each dangling id has to be repairable from the output."""
    _tree(
        tmp_path,
        cited={
            "tests/test_a.py": [DANGLING, DANGLING_TOO],
            "tests/test_b.py": [DANGLING_ELSEWHERE],
        },
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, result.stdout
    for req_id in (DANGLING, DANGLING_TOO, DANGLING_ELSEWHERE):
        assert req_id in result.stdout, f"{req_id!r} not named individually:\n{result.stdout}"


def test_id_defined_in_the_baseline_resolves(tmp_path: Path) -> None:
    _tree(
        tmp_path,
        baseline={"widgets": [IN_BASELINE, IN_BASELINE_TOO]},
        cited={"tests/test_widgets.py": [IN_BASELINE, IN_BASELINE_TOO]},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 0, result.stdout


def test_id_defined_only_by_a_pending_change_passes_but_is_reported(tmp_path: Path) -> None:
    """Acceptable today, and the exact state that rots silently, so it must be visible.

    A pending change is a real definition: the requirement is authored, under
    review, and on its way to the baseline. Failing on it would fail almost
    every citation in this repo. But it is provisional -- if the change is
    dropped, or archived without its deltas reaching the baseline, the citation
    becomes dangling -- so the run has to name the change it is leaning on.
    """
    _tree(
        tmp_path,
        pending={"add-widgets": {"widgets": [AUTHORED]}},
        cited={"tests/test_widgets.py": [AUTHORED]},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 0, result.stdout
    assert AUTHORED in result.stdout, "a pending-only citation was silent"
    assert "add-widgets" in result.stdout, "did not name the change the citation depends on"


def test_id_defined_only_by_an_archived_change_does_not_resolve(tmp_path: Path) -> None:
    """The bead's failure mode, reproduced.

    ``openspec archive --skip-specs`` moves the delta into
    ``openspec/changes/archive/`` and writes nothing to ``openspec/specs/``. An
    archived change has had its chance to land the requirement; if the baseline
    does not carry it, the citation has no canonical home. Counting
    ``openspec/changes/**`` as one flat definition source -- which archived
    changes are physically inside -- makes this case green and defeats the guard.
    """
    _tree(
        tmp_path,
        archived={"2026-01-01-add-widgets": {"widgets": [AUTHORED]}},
        cited={"tests/test_widgets.py": [AUTHORED]},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, f"an archived-but-unapplied id resolved:\n{result.stdout}"
    assert AUTHORED in result.stdout


def test_half_applied_archive_names_only_the_id_that_did_not_land(tmp_path: Path) -> None:
    """The case a capability-level check cannot see.

    One of the change's two cited requirements reached the baseline. Anything
    that asks "does this capability have a spec file?" or "did this archive
    write anything?" reports success here. The guard has to answer per id, and
    has to name the one that is missing without naming the one that landed.
    """
    _tree(
        tmp_path,
        baseline={"widgets": [AUTHORED]},
        archived={"2026-01-01-add-widgets": {"widgets": [AUTHORED, AUTHORED_TOO]}},
        cited={"tests/test_widgets.py": [AUTHORED, AUTHORED_TOO]},
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, f"half-applied archive reported clean:\n{result.stdout}"
    assert AUTHORED_TOO in result.stdout
    assert AUTHORED not in result.stdout, "named an id that did land"


def test_citations_in_roster_butler_tests_are_scanned(tmp_path: Path) -> None:
    """``testpaths`` is ``["tests", "roster"]``; butler tests live under roster/*/tests/."""
    _tree(tmp_path, cited={"roster/switchboard/tests/test_routing.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 1, f"roster butler tests were not scanned:\n{result.stdout}"
    assert DANGLING in result.stdout


def test_unqualified_req_shorthand_is_not_treated_as_a_citation(tmp_path: Path) -> None:
    """``REQ-005`` names no capability, so no lookup it could fail is well defined.

    Change-local shorthand like this appears in tests written against a single
    change's own numbering. It is not an OpenSpec id -- nothing emits it on an
    ``ID:`` line -- and reporting it as unresolved would be reporting a category
    error the reader cannot act on.
    """
    _tree(tmp_path, cited={"tests/test_widgets.py": [SHORTHAND]})
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 0, f"change-local shorthand was read as an id:\n{result.stdout}"


def test_trailing_punctuation_is_not_captured_into_the_id(tmp_path: Path) -> None:
    """An id ending a sentence is a citation of the id, not of the id plus a full stop."""
    _tree(tmp_path, baseline={"widgets": [IN_BASELINE]})
    test_file = tmp_path / "tests" / "test_widgets.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        f'def test_widget() -> None:\n    """Serves the widget per {IN_BASELINE}."""\n'
        "    assert True\n",
        encoding="utf-8",
    )
    result = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))

    assert result.returncode == 0, f"punctuation was captured into the id:\n{result.stdout}"


def test_frozen_entry_suppresses_a_known_dangling_citation(tmp_path: Path) -> None:
    _tree(tmp_path, cited={"tests/test_widgets.py": [DANGLING]})
    unfrozen = _run("--root", str(tmp_path), "--baseline", str(_empty_frozen(tmp_path)))
    assert unfrozen.returncode == 1

    frozen = _frozen(tmp_path, {"tests/test_widgets.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 0, result.stdout


def test_new_dangling_citation_fails_even_when_the_frozen_set_covers_the_same_file(
    tmp_path: Path,
) -> None:
    """The ratchet must not swallow drift that arrives after the freeze.

    A ratchet keyed on anything coarser than the individual ``(file, id)`` pair
    -- a count, a per-file flag -- goes green here, which would make the freeze a
    mute button rather than a floor.
    """
    _tree(tmp_path, cited={"tests/test_widgets.py": [DANGLING, DANGLING_TOO]})
    frozen = _frozen(tmp_path, {"tests/test_widgets.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 1, f"new drift swallowed by the ratchet:\n{result.stdout}"
    assert DANGLING_TOO in result.stdout
    assert f"{DANGLING} is defined nowhere" not in result.stdout, "re-reported a frozen entry"


def test_frozen_entry_does_not_travel_to_another_file(tmp_path: Path) -> None:
    """Freezing one file's debt must not license the same dangling id elsewhere."""
    _tree(
        tmp_path,
        cited={
            "tests/test_widgets.py": [DANGLING],
            "tests/test_gadgets.py": [DANGLING],
        },
    )
    frozen = _frozen(tmp_path, {"tests/test_widgets.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 1, f"a frozen entry licensed a second file:\n{result.stdout}"
    assert "tests/test_gadgets.py" in result.stdout


def test_repaired_frozen_entry_is_reported_for_hand_removal(tmp_path: Path) -> None:
    """There is no re-freeze flag, so a healed entry has to announce itself."""
    _tree(
        tmp_path,
        baseline={"widgets": [DANGLING]},
        cited={"tests/test_widgets.py": [DANGLING]},
    )
    frozen = _frozen(tmp_path, {"tests/test_widgets.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(frozen))

    assert result.returncode == 0, result.stdout
    assert "no longer dangle" in result.stdout, "a healed freeze entry was not announced"
    assert DANGLING in result.stdout


def test_strict_ignores_the_ratchet(tmp_path: Path) -> None:
    _tree(tmp_path, cited={"tests/test_widgets.py": [DANGLING]})
    frozen = _frozen(tmp_path, {"tests/test_widgets.py": [DANGLING]})
    result = _run("--root", str(tmp_path), "--baseline", str(frozen), "--strict")

    assert result.returncode == 1, f"--strict honoured the ratchet:\n{result.stdout}"
    assert DANGLING in result.stdout


def test_repo_scan_is_green_under_the_shipped_frozen_baseline() -> None:
    """CI on main must be green: the freeze covers every pre-existing dangling citation."""
    result = _run()

    assert result.returncode == 0, result.stdout[-4000:]


def test_the_requirement_that_prompted_this_guard_is_still_watched() -> None:
    """The bead's requirement must be inside the guard's field of view.

    This guard exists because 16 tests cite a requirement that lives only in an
    unarchived change. If that id is neither resolving nor reported as pending,
    the scan is not reaching the files that cite it and the guard is decorative.
    """
    result = _run()

    watched = _req("database-security-006")
    assert watched in result.stdout, (
        "the citation this guard was written for is not visible in its output:\n"
        + result.stdout[-4000:]
    )
