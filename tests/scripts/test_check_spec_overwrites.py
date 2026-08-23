"""Tests for scripts/check_spec_overwrites.py.

Regression guard for bu-s9uv3. ``openspec archive`` writes the WHOLE requirement
block from a change into the baseline, so a MODIFIED block authored against an
older ancestor silently deletes whatever the baseline gained in between. OpenSpec
1.9.0 only guards this at scenario-*name* granularity, which answers "were any
scenarios renamed or removed?" and was never positioned to answer "is this block
safe to archive?".

``tests/fixtures/spec_overwrite_regression/`` is the proof that the two questions
differ: it is the real instance-1 pair (``make-spend-forecasts-authoritative``'s
pre-rebuild Spend API block against the baseline as it stood once
``spend-ledger-truth`` archived), trimmed to the scenario names the two share.
``openspec validate --strict`` passes on it; archiving it would delete twelve
baseline clauses.
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
import check_spec_overwrites as gate  # noqa: E402

pytestmark = pytest.mark.unit

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "spec_overwrite_regression"
FIXTURE_CHANGE = "make-spend-forecasts-authoritative"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_spec_overwrites.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_spec(root: Path, spec: str, body: str) -> None:
    path = root / "openspec" / "specs" / spec / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _write_delta(root: Path, change: str, spec: str, body: str) -> None:
    path = root / "openspec" / "changes" / change / "specs" / spec / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


BASELINE_DOC = """# demo Specification

## Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
- **AND** every count is grouped from `public.widget_ledger`, so it describes the
  widget that actually did the work
"""


# --- The gate catches what the name-level guard cannot ------------------------


@pytest.mark.skipif(
    shutil.which("openspec") is None, reason="openspec CLI not installed on this machine"
)
def test_openspec_strict_validation_passes_on_the_body_overwrite_fixture() -> None:
    """The blind spot itself: strict validation is clean on a block that guts bodies.

    Every baseline scenario NAME survives in the fixture's block, which is all
    ``findMissingCurrentScenarios`` inspects, so OpenSpec reports the change as
    valid while twelve baseline clauses would be deleted on archive.
    """
    result = subprocess.run(
        [
            "openspec",
            "validate",
            FIXTURE_CHANGE,
            "--type",
            "change",
            "--strict",
            "--no-interactive",
        ],
        cwd=FIXTURE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "is valid" in result.stdout


def test_gate_rejects_the_body_overwrite_fixture(tmp_path: Path) -> None:
    """Same fixture, opposite verdict: the body comparison names each dropped clause."""
    empty_ratchet = tmp_path / "ratchet.json"
    empty_ratchet.write_text("{}\n", encoding="utf-8")

    result = _run("--root", str(FIXTURE), "--baseline", str(empty_ratchet))

    assert result.returncode == 1, result.stdout
    assert f'{FIXTURE_CHANGE} -> dashboard-spend-dashboard "Spend API"' in result.stdout
    # The clauses spend-ledger-truth contributed to the baseline, which this
    # block predates and would therefore delete.
    assert "public.token_usage_ledger" in result.stdout
    assert "ceiling_blind_to_unpriced_models" in result.stdout


def test_fixture_scenario_names_match_so_only_bodies_diverge() -> None:
    """Guards the fixture itself: a name drop would make it prove the wrong thing."""
    baseline = gate.baseline_requirements(
        (FIXTURE / "openspec/specs/dashboard-spend-dashboard/spec.md").read_text("utf-8")
    )["Spend API"]
    block = gate.modified_requirements(
        (
            FIXTURE
            / "openspec/changes"
            / FIXTURE_CHANGE
            / "specs/dashboard-spend-dashboard/spec.md"
        ).read_text("utf-8")
    )["Spend API"]

    baseline_names = [name for name, _ in gate.split_scenarios(baseline)[1]]
    block_names = [name for name, _ in gate.split_scenarios(block)[1]]

    assert baseline_names == block_names
    assert gate.find_losses(baseline, block)
    assert all(finding.kind != "scenario" for finding in gate.find_losses(baseline, block))


# --- The gate does not cry wolf on legitimate edits ---------------------------


def test_block_that_extends_a_baseline_clause_is_not_a_loss(tmp_path: Path) -> None:
    """Appending a qualifier to a clause keeps its content; only rewrites drop it."""
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints, and SHALL say so honestly.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]` with a nullable `total_cost`
- **AND** every count is grouped from `public.widget_ledger`, so it describes the
  widget that actually did the work
- **AND** an unpriced widget stays visibly unpriced
""",
    )

    found, _skipped = gate.collect(tmp_path)

    assert found == {}


def test_block_that_replaces_a_clause_body_is_a_loss(tmp_path: Path) -> None:
    """The bu-s9uv3 shape: one scenario name, wholly different body underneath."""
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
""",
    )

    found, _skipped = gate.collect(tmp_path)

    losses = found["demo-change/demo/Widget API"]
    assert [finding.kind for finding in losses] == ["clause"]
    assert "public.widget_ledger" in losses[0].excerpt


def test_modified_block_resolves_its_ancestor_through_a_rename(tmp_path: Path) -> None:
    """A change may rename and modify one requirement; the ancestor is the old name.

    Without the RENAMED lookup the block's new name finds no baseline, and the
    gate would skip the very block most likely to be rewriting a whole body.
    """
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## RENAMED Requirements

- FROM: `### Requirement: Widget API`
- TO: `### Requirement: Widget Endpoints`

## MODIFIED Requirements

### Requirement: Widget Endpoints
The dashboard SHALL expose the widget endpoints.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
""",
    )

    found, skipped = gate.collect(tmp_path)

    assert skipped == []
    assert "demo-change/demo/Widget Endpoints" in found


def test_requirement_header_inside_a_fence_does_not_open_a_block(tmp_path: Path) -> None:
    """A fenced sample of spec syntax is documentation, not a requirement."""
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints.

Authors write blocks like this:

```markdown
### Requirement: Widget API
#### Scenario: Widget totals
```

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
- **AND** every count is grouped from `public.widget_ledger`, so it describes the
  widget that actually did the work
""",
    )

    found, skipped = gate.collect(tmp_path)

    assert found == {}
    assert skipped == []


# --- The ratchet freezes today's debt without going deaf ----------------------


def test_frozen_loss_is_silent_until_the_baseline_moves_under_it(tmp_path: Path) -> None:
    """The archive-arms-a-collision case, which is why the ratchet is digest-keyed.

    A frozen loss stays quiet. When another change archives and the baseline
    requirement gains content, the unarchived block's losses change identity and
    the gate fires again -- exactly the moment PR #3755 armed instance 3.
    """
    tree = tmp_path / "tree"
    _write_spec(tree, "demo", BASELINE_DOC)
    _write_delta(
        tree,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
""",
    )
    ratchet = tmp_path / "ratchet.json"

    frozen = _run("--root", str(tree), "--baseline", str(ratchet), "--update-baseline")
    assert frozen.returncode == 0, frozen.stdout
    assert json.loads(ratchet.read_text("utf-8"))["demo-change/demo/Widget API"]

    quiet = _run("--root", str(tree), "--baseline", str(ratchet))
    assert quiet.returncode == 0, quiet.stdout

    # Another change archives, adding a clause to the same baseline requirement.
    _write_spec(
        tree,
        "demo",
        BASELINE_DOC + "- **AND** a widget with no runs reports `null`, never `0`\n",
    )

    armed = _run("--root", str(tree), "--baseline", str(ratchet))
    assert armed.returncode == 1, armed.stdout
    assert "never `0`" in armed.stdout


def test_repo_tree_carries_no_unfrozen_baseline_losses() -> None:
    """The checked-in ratchet must describe the tree exactly, or the gate is red in CI."""
    result = _run()

    assert result.returncode == 0, result.stdout


# --- Pending-parent lineage: the baseline may not exist yet (bu-9w5eu) --------
#
# A MODIFIED block may target a capability that no baseline file carries yet
# because the requirement is created by an unarchived SIBLING change's ADDED
# block. The pair archives fine in order. Resolving the ancestor from
# ``openspec/specs/`` alone misses it, and the miss used to skip the body
# comparison outright -- so the block most able to gut a brand-new requirement
# got the least scrutiny.

PARENT_ADDED_DOC = """## ADDED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]`
- **AND** every count is grouped from `public.gadget_ledger`, so it describes the
  gadget that actually did the work

#### Scenario: Gadget drill-down
- **WHEN** a caller opens one gadget
- **THEN** the occurrences resolve against the same ledger the summary used
"""


def test_pending_parent_modified_is_compared_against_its_siblings_added_block(
    tmp_path: Path,
) -> None:
    """A superset of the pending parent's block is clean -- and stops being noted.

    Pins the no-cry-wolf half: the ancestor RESOLVES through the sibling, so the
    skip note disappears rather than being emitted for a legitimate pattern.
    """
    _write_delta(tmp_path, "parent-change", "gadgets", PARENT_ADDED_DOC)
    _write_delta(
        tmp_path,
        "child-change",
        "gadgets",
        """## MODIFIED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints, and SHALL say so honestly.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]` with a nullable `total_cost`
- **AND** every count is grouped from `public.gadget_ledger`, so it describes the
  gadget that actually did the work

#### Scenario: Gadget drill-down
- **WHEN** a caller opens one gadget
- **THEN** the occurrences resolve against the same ledger the summary used
- **AND** an unpriced gadget stays visibly unpriced
""",
    )

    found, skipped = gate.collect(tmp_path)

    assert found == {}
    assert skipped == []


def test_pending_parent_modified_that_drops_a_sibling_clause_is_caught(
    tmp_path: Path,
) -> None:
    """The coverage the skip forgave: a clause the pending parent introduced, deleted.

    This is the whole point of the fallback. Delete the sibling lookup and this
    block resolves to no ancestor, the gate skips it, ``found`` is empty, and the
    deletion ships.
    """
    _write_delta(tmp_path, "parent-change", "gadgets", PARENT_ADDED_DOC)
    _write_delta(
        tmp_path,
        "child-change",
        "gadgets",
        """## MODIFIED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]`

#### Scenario: Gadget drill-down
- **WHEN** a caller opens one gadget
- **THEN** the occurrences resolve against the same ledger the summary used
""",
    )

    found, skipped = gate.collect(tmp_path)

    losses = found["child-change/gadgets/Gadget API"]
    assert "public.gadget_ledger" in " ".join(finding.excerpt for finding in losses)
    assert skipped == []


def test_pending_parent_modified_that_drops_a_whole_sibling_scenario_is_caught(
    tmp_path: Path,
) -> None:
    """Scenario-level deletion against a pending parent, which no baseline file holds.

    OpenSpec's own ``findMissingCurrentScenarios`` compares against
    ``openspec/specs/`` too, so for a not-yet-archived parent this drop is
    invisible to the CLI as well -- the gate is the only thing that can see it.
    """
    _write_delta(tmp_path, "parent-change", "gadgets", PARENT_ADDED_DOC)
    _write_delta(
        tmp_path,
        "child-change",
        "gadgets",
        """## MODIFIED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]`
- **AND** every count is grouped from `public.gadget_ledger`, so it describes the
  gadget that actually did the work
""",
    )

    found, skipped = gate.collect(tmp_path)

    losses = found["child-change/gadgets/Gadget API"]
    assert any(finding.kind == "scenario" for finding in losses)
    assert "Gadget drill-down" in " ".join(finding.scenario or "" for finding in losses)
    assert skipped == []


def test_live_baseline_wins_over_a_sibling_added_block_of_the_same_name(
    tmp_path: Path,
) -> None:
    """The fallback is a fallback: an archived baseline is still the ancestor.

    Without this, adding the sibling lookup could silently re-point blocks that
    already resolved correctly, which would move findings the ratchet has frozen.
    """
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "decoy-change",
        "demo",
        """## ADDED Requirements

### Requirement: Widget API
Something else entirely, with no clause the baseline carries.

#### Scenario: Widget totals
- **WHEN** an unrelated call happens
- **THEN** an unrelated thing is true
""",
    )
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Widget API
The dashboard SHALL expose the widget endpoints.

#### Scenario: Widget totals
- **WHEN** `GET /api/widgets` is called
- **THEN** the response is `ApiResponse[WidgetSummary]`
""",
    )

    found, _skipped = gate.collect(tmp_path)

    # Resolved against the real baseline, so the loss is the baseline's clause.
    losses = found["demo-change/demo/Widget API"]
    assert "public.widget_ledger" in losses[0].excerpt


def test_requirement_with_no_baseline_and_no_sibling_still_reports_the_skip(
    tmp_path: Path,
) -> None:
    """A genuine orphan keeps its note; the fallback narrows the notes, not removes them."""
    _write_spec(tmp_path, "demo", BASELINE_DOC)
    _write_delta(
        tmp_path,
        "demo-change",
        "demo",
        """## MODIFIED Requirements

### Requirement: Nonexistent API
The dashboard SHALL expose something no baseline and no sibling declares.

#### Scenario: Nothing
- **WHEN** nothing happens
- **THEN** nothing is true
""",
    )

    found, skipped = gate.collect(tmp_path)

    assert found == {}
    assert skipped == [
        'demo-change/demo: MODIFIED "Nonexistent API" has no baseline requirement to overwrite'
    ]


def test_two_siblings_adding_one_requirement_are_all_compared_against(
    tmp_path: Path,
) -> None:
    """Ambiguous lineage: satisfy every candidate, because only one can ever archive.

    Two unarchived changes ADDing the same heading for one capability is itself
    an error -- ``openspec archive`` aborts the second with "already exists" --
    so exactly one will become the baseline and the gate cannot know which.
    Reporting the union of the candidates' losses is the only answer that is safe
    under either outcome. Picking one silently would make the verdict depend on
    directory ordering.
    """
    _write_delta(tmp_path, "parent-a", "gadgets", PARENT_ADDED_DOC)
    _write_delta(
        tmp_path,
        "parent-b",
        "gadgets",
        """## ADDED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]`
- **AND** the payload carries `public.rival_ledger` provenance
""",
    )
    _write_delta(
        tmp_path,
        "child-change",
        "gadgets",
        """## MODIFIED Requirements

### Requirement: Gadget API
The dashboard SHALL expose the gadget endpoints.

#### Scenario: Gadget totals
- **WHEN** `GET /api/gadgets` is called
- **THEN** the response is `ApiResponse[GadgetSummary]`
""",
    )

    found, skipped = gate.collect(tmp_path)

    excerpts = " ".join(f.excerpt for f in found["child-change/gadgets/Gadget API"])
    # Both candidates' dropped clauses are reported, not whichever sorts first.
    assert "public.gadget_ledger" in excerpts
    assert "public.rival_ledger" in excerpts
    # And the ambiguity itself is surfaced rather than resolved silently.
    assert any("parent-a" in note and "parent-b" in note for note in skipped)
