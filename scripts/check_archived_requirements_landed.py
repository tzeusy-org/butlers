#!/usr/bin/env python3
"""
check_archived_requirements_landed.py

Detect archived OpenSpec changes whose requirements never reached
``openspec/specs/``.

Why this exists
---------------
Archiving a change is supposed to write its requirements into the baseline.
Nothing verified that it did. Measured at introduction: 488 ``## ADDED``
requirements across 44 archived changes were never written to
``openspec/specs/`` at all, and counting ``## MODIFIED`` content that never
landed the total comes to 1067 units across 666 requirements in 96 changes --
undetected for months, while every check went green (bu-966by, bu-tk618).

Each existing signal was asked a question it was never positioned to answer:

* ``openspec validate --changes --strict`` passes on a change with every one of
  its requirements missing from the baseline. It validates delta *syntax*, not
  application.
* ``openspec archive`` prints ``Totals: + N``, which reports what it wrote and
  never what it skipped. In the case that surfaced this
  (``2026-05-19-redesign-ingestion-dispatch-console``) the archive commit was a
  hand-run ``git mv`` and the tool was not invoked at all, so there was no
  output to be wrong.
* ``check_spec_overwrites.py`` reads ``openspec/changes/**`` only, so it is
  blind both to baseline hand-edits and to archives that wrote no baseline.

This gate reads the other side of the ledger: for every archived change it
asserts the requirements it claims to have delivered are present in the
baseline today.

What it checks
--------------
Per ``openspec/changes/archive/<change>/specs/<capability>/spec.md``:

* every ``### Requirement:`` under ``## ADDED Requirements`` must exist under
  that exact name in ``openspec/specs/<capability>/spec.md``;
* every ``## MODIFIED Requirements`` block must have its content present in the
  baseline requirement it targets -- the same clause comparison
  ``check_spec_overwrites.py`` runs, with the arguments the other way round.
  There the question is "does the block drop baseline content?"; here it is
  "did the block's content reach the baseline?".

Findings are reported one requirement at a time, by name. That is the whole
point: a change-level verdict ("did this archive write anything?") goes green on
a *half*-applied archive, which is the same defect shape as the signals above.

Renames and removals
--------------------
A requirement legitimately disappears from the baseline when a later change
renames or removes it. Both are honoured, but they draw evidence from different
places, because they carry different risk.

A **rename** is read from any change in the tree, archived or not, and is
ordering-agnostic: if some change renames ``A`` to ``B`` in that capability,
``B``'s presence in the baseline satisfies ``A``. Archive directory names are
not reliably ordered (not all carry a date prefix), and a rename cannot silence
anything on its own -- it only redirects the baseline lookup, so an absent ``B``
still fires.

A **removal** is read from archived changes only. Unlike a rename it is an
unconditional skip: nothing is looked up afterwards, so a ``## REMOVED`` block
suppresses the finding outright. Honouring one from an unarchived change would
let a pending proposal -- possibly never archived, possibly abandoned -- mute a
real gap permanently, without a ratchet entry and without a reviewer ever seeing
a JSON diff. That is the same shape as the defect this guard exists to catch: a
check credited with an answer it was never positioned to give. A removal earns
its authority by having actually archived.

Baseline ratchet
----------------
The pre-existing gaps mean this cannot be introduced as a hard gate, so
``archived-requirements-baseline.json`` freezes them per
``(change, spec, requirement)`` and content-addressed per finding; the gate
fails only on *new* drift. Burning the frozen set down is bu-tk618.

There is deliberately **no** ``--update-baseline`` flag. A guard that can
re-freeze itself is one command away from meaning nothing, which is how the
frozen set it was cloned from lost its teeth. Entries come out of the JSON by
hand, as they are repaired; the gate prints which ones are now healed.

Limits
------
Requirement matching is by exact header name. A requirement that landed under a
reworded name and was never recorded as a RENAME reads as missing. Clause
matching inherits ``check_spec_overwrites``'s heuristics, so treat a green run
as "nothing obviously unapplied", not as proof the archive was faithful.

Usage:
  python3 scripts/check_archived_requirements_landed.py             # gate (ratchet)
  python3 scripts/check_archived_requirements_landed.py --strict    # ignore ratchet
  python3 scripts/check_archived_requirements_landed.py --root DIR  # scan another tree

Exit codes:
  0  Every archived requirement landed, or the gap is frozen.
  1  At least one archived requirement is missing from the baseline unfrozen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "archived-requirements-baseline.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_spec_overwrites import (  # noqa: E402
    Finding,
    baseline_requirements,
    digest_of,
    find_losses,
    parse_document,
    rename_map,
)

# Kind for "the requirement header itself never appeared in the baseline", as
# opposed to the prose/scenario/clause kinds find_losses produces for a block
# whose target requirement does exist.
MISSING_REQUIREMENT = "requirement"


def requirements_under(text: str, section_word: str) -> dict[str, list[str]]:
    """Requirements under every ``## ...<section_word>...`` header of a delta."""
    selected: dict[str, list[str]] = {}
    for section, requirements in parse_document(text).items():
        if section and section_word in section.upper():
            selected.update(requirements)
    return selected


def resolve_name(name: str, renames: dict[str, str]) -> str:
    """Follow ``{old: new}`` to the name a requirement should carry today."""
    seen = {name}
    current = name
    while current in renames:
        current = renames[current]
        if current in seen:  # a rename cycle; stop rather than spin
            break
        seen.add(current)
    return current


def build_rename_and_removal_index(
    changes_dir: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """``({spec: {old: new}}, {spec: {removed names}})`` for excusing an absence.

    Renames come from every change in the tree; removals only from archived
    ones. See the module docstring: a rename merely redirects the lookup, so
    trusting a pending one costs nothing, while a removal skips the check
    outright and a pending change must not be able to do that.
    """
    archive_dir = changes_dir / "archive"
    renames: dict[str, dict[str, str]] = {}
    removals: dict[str, set[str]] = {}

    for delta_file in sorted(changes_dir.rglob("specs/*/spec.md")):
        spec = delta_file.parent.name
        text = delta_file.read_text("utf-8")
        for new, old in rename_map(text).items():
            renames.setdefault(spec, {})[old] = new
        if archive_dir in delta_file.parents:
            for removed in requirements_under(text, "REMOVED"):
                removals.setdefault(spec, set()).add(removed)

    return renames, removals


def collect(root: Path) -> dict[str, list[Finding]]:
    """Scan every archived change; return ``{change/spec/requirement: findings}``."""
    specs_dir = root / "openspec" / "specs"
    changes_dir = root / "openspec" / "changes"
    archive_dir = changes_dir / "archive"

    baselines: dict[str, dict[str, list[str]]] = {}
    if specs_dir.is_dir():
        for spec_file in sorted(specs_dir.glob("*/spec.md")):
            baselines[spec_file.parent.name] = baseline_requirements(spec_file.read_text("utf-8"))

    renames, removals = (
        build_rename_and_removal_index(changes_dir) if changes_dir.is_dir() else ({}, {})
    )

    found: dict[str, list[Finding]] = {}
    if not archive_dir.is_dir():
        return found

    for change_dir in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
        for delta_file in sorted(change_dir.glob("specs/*/spec.md")):
            spec = delta_file.parent.name
            text = delta_file.read_text("utf-8")
            baseline = baselines.get(spec, {})
            spec_renames = renames.get(spec, {})
            spec_removals = removals.get(spec, set())

            def landed(requirement: str) -> list[str] | None:
                """The baseline body for ``requirement``, or None if it is gone."""
                return baseline.get(resolve_name(requirement, spec_renames))

            for requirement in requirements_under(text, "ADDED"):
                if requirement in spec_removals or landed(requirement) is not None:
                    continue
                found.setdefault(f"{change_dir.name}/{spec}/{requirement}", []).append(
                    Finding(MISSING_REQUIREMENT, None, digest_of(requirement), requirement)
                )

            for requirement, block_body in requirements_under(text, "MODIFIED").items():
                if requirement in spec_removals:
                    continue
                baseline_body = landed(requirement)
                key = f"{change_dir.name}/{spec}/{requirement}"
                if baseline_body is None:
                    found.setdefault(key, []).append(
                        Finding(MISSING_REQUIREMENT, None, digest_of(requirement), requirement)
                    )
                    continue
                # Arguments reversed against check_spec_overwrites' own use:
                # what we want is the delta's content missing from the baseline.
                unapplied = find_losses(block_body, baseline_body)
                if unapplied:
                    found.setdefault(key, []).extend(unapplied)

    return found


def describe(finding: Finding) -> str:
    """One line of failure text, phrased for "did this land?" rather than "was this dropped?"."""
    if finding.kind == MISSING_REQUIREMENT:
        return f'requirement "{finding.excerpt}" is not in the baseline'
    if finding.kind == "scenario":
        return f'scenario "{finding.scenario}" is not in the baseline'
    where = f'scenario "{finding.scenario}"' if finding.scenario else "requirement prose"
    return f"{where} did not land: {finding.excerpt}"


def load_baseline(path: Path) -> dict[str, list[Finding]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text("utf-8"))
    return {key: [Finding.from_json(item) for item in items] for key, items in raw.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when an archived change's requirements are missing from openspec/specs/."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Tree containing openspec/ to scan (default: repo root).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Ratchet file of already-known gaps (default: %(default)s).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the ratchet; any unlanded requirement fails.",
    )
    args = parser.parse_args()

    found = collect(args.root)
    frozen = {} if args.strict else load_baseline(args.baseline)

    regressions: list[str] = []
    healed: list[str] = []
    total = 0

    for key, findings in sorted(found.items()):
        allowed = {finding.key() for finding in frozen.get(key, [])}
        new = [finding for finding in findings if finding.key() not in allowed]
        if not new:
            continue
        regressions.append(key)
        total += len(new)
        change, spec, requirement = key.split("/", 2)
        print(f'\n{change} -> {spec} "{requirement}"')
        for finding in new:
            print(f"  {describe(finding)}")

    for key, findings in sorted(frozen.items()):
        current = {finding.key() for finding in found.get(key, [])}
        gone = [finding for finding in findings if finding.key() not in current]
        if gone and key not in regressions:
            healed.append(f"{key} ({len(gone)} fewer)")

    if healed:
        print(
            "\nFrozen gaps that have since been repaired: "
            + ", ".join(healed)
            + "\n  Delete these entries from "
            + str(args.baseline.name)
            + " by hand (bu-tk618). There is no re-freeze flag on purpose."
        )

    if regressions:
        print(
            f"\n{total} archived requirement(s) across {len(regressions)} entr(ies) never reached "
            "openspec/specs/. Each name above was declared delivered by an archived change and is "
            "not in the baseline today. Apply the change's deltas to the baseline through "
            "`openspec archive` on a restored copy. If the requirement was superseded instead, "
            "record that in the superseding change -- a `## RENAMED` block counts wherever it "
            "lives, but a `## REMOVED` block excuses the absence only once that change has "
            "itself been archived."
        )
        return 1

    print(f"Every archived requirement landed ({len(frozen)} frozen entr(ies) still outstanding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
