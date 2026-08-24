#!/usr/bin/env python3
"""
check_cited_requirements_resolve.py

Detect tests that cite an OpenSpec requirement id with no definition behind it.

Why this exists
---------------
Test docstrings cite requirement ids (``REQ-database-security-006``) as the
statement of what the test is for. Nothing checked that the cited id exists.
Measured at introduction: 39 distinct ids are cited across the test tree, 30 of
them resolve *only* to a change that has not archived yet, and one
(``REQ-cli-runtime-auth-003``) resolves nowhere at all -- there is no
``cli-runtime-auth`` capability in the repo (bu-lpwjc).

The two existing spec guards read the spec tree against itself and have never
opened a test file:

* ``check_spec_overwrites.py`` asks whether an unarchived ``## MODIFIED`` block
  would delete baseline content when it archives.
* ``check_archived_requirements_landed.py`` asks whether an archived change's
  requirements reached the baseline.

Both take the citing side for granted. So a requirement can be renumbered,
dropped with its change, or archived without ever reaching
``openspec/specs/``, and the tests that name it keep passing while naming
nothing.

What it checks
--------------
Every ``REQ-<capability>-<number>`` token in a test file must resolve to an
``ID:`` line in a spec document. Findings are reported one citation at a time,
by file and id -- an aggregate count is not repairable.

Definition sources, and why an archived change is not one
---------------------------------------------------------
Two sources resolve a citation, and the distinction is the whole guard:

* ``openspec/specs/<capability>/spec.md`` -- the canonical baseline. Resolving
  here is the settled state.
* ``openspec/changes/<change>/specs/<capability>/spec.md`` for a change that has
  **not** archived -- provisional. The requirement is authored and on its way to
  the baseline. This passes, because failing it would fail almost every citation
  in the repo, but every such citation is printed with the change it leans on:
  that is the state that rots, and it rots silently otherwise.

``openspec/changes/archive/**`` is deliberately **not** a source. An archived
change has already had its chance to write the requirement into the baseline;
if the baseline does not carry it, the citation has no canonical home. Counting
the archive would make this guard blind to precisely the failure it was written
for -- ``openspec archive --skip-specs`` moves the delta under ``archive/`` and
writes nothing to ``openspec/specs/``, so a flat reading of
``openspec/changes/**`` keeps reporting the citation as resolved forever. It is
also what makes a *half*-applied archive visible: the ids that landed resolve
via the baseline and the ones that did not are named, which no
capability-level or change-level verdict can do.

What counts as a citation
-------------------------
``REQ-`` followed by at least one lowercase-alphanumeric capability segment and
a numeric suffix, which is the shape ``ID:`` lines carry. Change-local shorthand
(``REQ-004``, ``REQ-005``) is excluded on purpose: it names no capability, so
there is no lookup it could fail, and reporting it would be reporting a category
error rather than a defect. The trade is real -- a citation that misspells the
capability into something unrecognisable is invisible -- but the alternative is
noise nobody can act on.

Baseline ratchet
----------------
``cited-requirements-baseline.json`` freezes today's dangling citations per
``(test file, requirement id)`` so the gate fails only on *new* drift. The pair
is the key rather than the id or the file alone: freezing one file's debt must
not license the same id somewhere else, and a per-file flag would swallow the
next dangling id added to a file already on the list.

There is deliberately **no** ``--update-baseline`` flag, for the reason its
sibling guards give: a guard that can re-freeze itself is one command away from
meaning nothing. Entries come out by hand as they are repaired, and the gate
prints which ones have healed.

Limits
------
Only test files are scanned (``tests/`` and ``roster/*/tests/``, matching
``testpaths``). ``src/`` and ``roster/`` implementation modules cite requirement
ids too and are not covered here. Resolution is by exact id, so a requirement
that was renumbered rather than removed reads as dangling -- which is the
intended reading, since the citation is then pointing at nothing.

Usage:
  python3 scripts/check_cited_requirements_resolve.py             # gate (ratchet)
  python3 scripts/check_cited_requirements_resolve.py --strict    # ignore ratchet
  python3 scripts/check_cited_requirements_resolve.py --root DIR  # scan another tree

Exit codes:
  0  Every cited requirement id resolves, or the gap is frozen.
  1  At least one citation resolves to no definition and is not frozen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "cited-requirements-baseline.json"

# A qualified requirement id: capability segments then a number, the shape an
# `ID:` line carries. Unqualified shorthand (`REQ-005`) does not match; see the
# module docstring.
CITATION = re.compile(r"\bREQ-(?:[a-z0-9]+-)+[0-9]+\b")

# The declaration form OpenSpec requirement blocks use, anchored to its own line
# so that prose *mentioning* an id (a tasks.md instruction to cite it, say) is
# not mistaken for a definition of it.
DEFINITION = re.compile(r"^ID:\s*(REQ-\S+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Citation:
    """One requirement id named in one test file."""

    path: str  # relative to the scanned root, POSIX-separated
    line: int
    req_id: str

    def key(self) -> tuple[str, str]:
        return (self.path, self.req_id)


def definitions_in(spec_files: list[Path]) -> set[str]:
    """Every requirement id declared by an ``ID:`` line in ``spec_files``."""
    declared: set[str] = set()
    for spec_file in spec_files:
        declared.update(DEFINITION.findall(spec_file.read_text("utf-8", errors="ignore")))
    return declared


def baseline_definitions(root: Path) -> set[str]:
    """Requirement ids in ``openspec/specs`` -- the canonical, settled source."""
    return definitions_in(sorted((root / "openspec" / "specs").glob("*/spec.md")))


def pending_definitions(root: Path) -> dict[str, set[str]]:
    """``{change: {requirement id}}`` for changes that have not archived yet.

    The archive is excluded, not overlooked: see the module docstring.

    The walk is ``rglob`` rather than a shallow ``glob("*/specs/*/spec.md")``
    deliberately. A shallow pattern cannot reach an archived delta anyway --
    ``archive/<change>/specs/<cap>/spec.md`` is one level deeper -- so the
    exclusion below would be dead code, and the guard's load-bearing rule would
    hold by accident of a glob shape rather than by saying so. Reaching the
    archived files and skipping them explicitly is what makes the rule survive
    someone rewriting this walk.
    """
    changes_dir = root / "openspec" / "changes"
    archive_dir = changes_dir / "archive"
    pending: dict[str, set[str]] = {}
    for delta_file in sorted(changes_dir.rglob("specs/*/spec.md")):
        if archive_dir in delta_file.parents:
            continue
        change = delta_file.relative_to(changes_dir).parts[0]
        declared = definitions_in([delta_file])
        if declared:
            pending.setdefault(change, set()).update(declared)
    return pending


def test_files(root: Path) -> list[Path]:
    """Every Python file under a ``tests`` directory, matching pytest's ``testpaths``.

    ``testpaths = ["tests", "roster"]``, and butler tests live at
    ``roster/<butler>/tests/``, so the directory name is the reliable marker
    rather than a fixed top-level list. Dot-prefixed directories are out of
    scope: a worker's ``.worktrees/`` copy of the repo is a *different* checkout
    whose tests cite whatever that branch is mid-way through changing, and whose
    ``openspec/`` tree is a different tree.

    They are pruned during the walk rather than filtered out of its results,
    which is a cost decision, not a correctness one -- a post-filter reaches the
    same answer. AGENTS.md puts a full checkout, ``.venv`` and all, under
    ``.worktrees/parallel-agents/<id>/``. Measured on one such repo root:
    descending into them and discarding them afterwards traverses 2.3M entries
    in ~19s to select the same ~1.2k files a pruning walk reaches in 0.3s, and
    the whole guard did not finish inside 120s. CI checks out a clean tree and
    never pays it, so the cost falls entirely on the developer running this
    before pushing -- the one run that has to be cheap enough to actually happen.
    """
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(root):
        # In-place, so os.walk itself never descends -- this is the whole point.
        subdirectories[:] = [name for name in subdirectories if not name.startswith(".")]
        if "tests" not in Path(directory).relative_to(root).parts:
            continue
        found.extend(
            Path(directory) / name
            for name in names
            if name.endswith(".py") and not name.startswith(".")
        )
    return sorted(found)


def collect(root: Path) -> list[Citation]:
    """Every qualified requirement id cited by a test file, with where it was cited.

    Deduplicated per ``(file, line, id)``: naming the same id twice for one line
    is two lines of output and one repair.
    """
    found: dict[Citation, None] = {}
    for path in test_files(root):
        relative = path.relative_to(root).as_posix()
        for number, text in enumerate(path.read_text("utf-8", errors="ignore").splitlines(), 1):
            for req_id in CITATION.findall(text):
                found.setdefault(Citation(relative, number, req_id))
    return list(found)


def load_baseline(path: Path) -> set[tuple[str, str]]:
    """Frozen ``(test file, requirement id)`` pairs."""
    if not path.exists():
        return set()
    raw = json.loads(path.read_text("utf-8"))
    return {(file, req_id) for file, req_ids in raw.items() for req_id in req_ids}


def report_pending(pending_cited: dict[str, list[Citation]]) -> None:
    """Name every change a citation is leaning on, and how much leans on it."""
    if not pending_cited:
        return
    print("Citations resolved only by a change that has not archived yet:")
    for change, citations in sorted(pending_cited.items()):
        ids = sorted({citation.req_id for citation in citations})
        files = len({citation.path for citation in citations})
        print(f"  {change}")
        print(f"    {', '.join(ids)}  ({len(citations)} citation(s) in {files} file(s))")
    print(
        "  These pass: the requirement is authored and on its way to the baseline. They "
        "become failures if the change is dropped, or archived without its deltas reaching "
        "openspec/specs/ -- which is the point of naming them here.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a test cites a REQ id with no canonical definition."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Tree containing openspec/ and the tests to scan (default: repo root).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Ratchet file of already-known dangling citations (default: %(default)s).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the ratchet; any unresolved citation fails.",
    )
    args = parser.parse_args()

    citations = collect(args.root)
    canonical = baseline_definitions(args.root)
    pending = pending_definitions(args.root)
    pending_owner = {req_id: change for change, ids in pending.items() for req_id in ids}
    frozen = set() if args.strict else load_baseline(args.baseline)

    unresolved: dict[str, list[Citation]] = {}
    pending_cited: dict[str, list[Citation]] = {}
    for citation in citations:
        if citation.req_id in canonical:
            continue
        change = pending_owner.get(citation.req_id)
        if change is not None:
            pending_cited.setdefault(change, []).append(citation)
        elif citation.key() not in frozen:
            unresolved.setdefault(citation.path, []).append(citation)

    report_pending(pending_cited)

    resolved_keys = {
        citation.key()
        for citation in citations
        if citation.req_id in canonical or citation.req_id in pending_owner
    }
    cited_keys = {citation.key() for citation in citations}
    healed = sorted(
        f"{file} -> {req_id}"
        for file, req_id in frozen
        if (file, req_id) in resolved_keys or (file, req_id) not in cited_keys
    )
    if healed:
        print(
            "Frozen citations that no longer dangle: "
            + ", ".join(healed)
            + f"\n  Delete these entries from {args.baseline.name} by hand. There is no "
            "re-freeze flag on purpose.\n"
        )

    if unresolved:
        total = sum(len(items) for items in unresolved.values())
        for path, items in sorted(unresolved.items()):
            print(f"\n{path}")
            for citation in sorted(items, key=lambda c: (c.line, c.req_id)):
                print(f"  line {citation.line}: {citation.req_id} is defined nowhere")
        print(
            f"\n{total} citation(s) across {len(unresolved)} test file(s) name a requirement id "
            "with no definition in openspec/specs/ and none in an unarchived change. Either the "
            "id is wrong in the test, or the requirement never reached the baseline: an archived "
            "change does not count as a definition, because archiving is exactly when it was "
            "supposed to land there. Fix the citation or apply the change's deltas through "
            "`openspec archive` on a restored copy."
        )
        return 1

    print(
        f"Every cited requirement id resolves ({len(frozen)} frozen citation(s) still outstanding)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
