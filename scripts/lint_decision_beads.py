#!/usr/bin/env python3
"""Enforce the decision-bead convention (bu-ckkpz.1, epic bu-ckkpz "Owner
Decision Desk").

See AGENTS.md > "Decision-bead convention" for the full convention writeup
and docs/redesigns/2026-07-10-jarvis-pursuit.md §8 for the originating epic.

The convention, in one line: a bead marked as an owner decision must carry
*structured* options, a default, and a deadline -- not just a title that
happens to say "DECISION REQUIRED (owner)". Concretely, a decision bead is
any issue that:

  1. carries the ``decision`` label,
  2. has ``metadata.decision.options`` -- a non-empty list of distinct,
     non-blank strings,
  3. has ``metadata.decision.default`` -- a non-blank string that exactly
     matches one entry in ``options`` (the owner-silent fallback), and
  4. has ``due_at`` set (bd's native due-date field, e.g. ``bd create --due
     2026-07-25`` / ``bd update <id> --due +2w``) -- the decision deadline.

Why metadata + due_at instead of description section headers (the pattern
`bd lint` already uses for bug/task/epic)? bd's own ``issue_type: decision``
is already taken by an unrelated ADR-style "decision already made" template
(``--validate`` on ``--type decision`` demands ``## Decision`` / ``##
Rationale`` / ``## Alternatives Considered``) -- reusing it here would collide
with that template and misdescribe a *pending* decision as an already-made
one. ``due_at`` is bd's own deadline field (filterable via ``--due-before``/
``--overdue``); reusing it avoids inventing a second, text-only deadline
format. ``metadata`` gives real structured data (a list + a scalar) instead
of a markdown blob a future consumer (the dashboard Decisions lane, the
Telegram one-tap-close flow in bu-ckkpz.3) would have to regex back apart.

This script does not touch bd's own ``bd lint`` (which is unaware of the
``decision`` label and cannot be extended with custom per-label section
rules from repo config) -- it is a separate, repo-owned check, run locally
against live bd/Dolt data. It is intentionally NOT wired into CI: GitHub
Actions runners cannot reach the Dolt server backing `bd` (see AGENTS.md
"Beads DB Mode"), so there is no live bead data for a CI job to check.

Non-vacuous checking (bu-hmdqz.6, ``--check-unlabeled-markers``)
------------------------------------------------------------------
By default this script only checks issues that ALREADY carry the
``decision`` label (via ``bd list --label decision`` discovery, an
``--issues-json-file`` snapshot, or explicit issue IDs) -- so against a
queue where zero beads have adopted the label yet, it discovers zero rows
and reports a vacuous "clean" pass. ``--check-unlabeled-markers`` widens
*discovery only* (never explicit issue IDs, which are always checked as
given) to also include open, non-epic beads whose titles match a legacy
decision marker (``DECISION REQUIRED``, ``OWNER-GATED``, ``OWNER DECISION``,
``ARCHITECTURAL DECISION``, ``OWNER:``) but lack the label -- see
:data:`_DECISION_TITLE_MARKERS` and :func:`is_unlabeled_marker_match`. Those
beads then fail :func:`lint_issue`'s existing "missing 'decision' label"
check the same way a labeled-but-incomplete bead fails its other checks,
which is what makes the lint non-vacuous against a live, pre-migration
queue. This is also what ``src/butlers/jobs/decision_review.py``'s weekly
digest job runs (via ``--issues-json-file`` against the mounted export) to
flag an attention-ledger row when the convention has unmigrated adopters.

Usage:
  python3 scripts/lint_decision_beads.py                  # lint all open decision beads
  python3 scripts/lint_decision_beads.py bu-v4ipc bu-zhfd0 # lint specific issues
  python3 scripts/lint_decision_beads.py --status all      # include closed issues
  python3 scripts/lint_decision_beads.py --json            # machine-readable output
  python3 scripts/lint_decision_beads.py --issues-json-file snapshot.json  # offline input
  python3 scripts/lint_decision_beads.py --check-unlabeled-markers         # non-vacuous mode

Exit codes:
  0  No violations found.
  1  One or more decision beads fail the convention.
  2  Could not obtain issue data (bd unavailable, bad JSON, etc.) -- never
     reported as a passing lint; fail closed rather than fabricate a clean run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DECISION_LABEL = "decision"

# Mirrors `_DECISION_TITLE_MARKERS` / `_OPEN_STATUSES` in
# src/butlers/jobs/decision_review.py exactly (epic-exclusion included).
# Deliberately duplicated rather than imported -- this script is designed to
# run standalone (offline `--issues-json-file` mode, no live DB) and stays
# independent of the `butlers` package's DB-touching import chain, the same
# rationale decision_review.py itself gives for duplicating DECISION_LABEL
# in the other direction (see that module's "Decision-bead detection"
# docstring section). Keep both patterns in sync if either changes.
_DECISION_TITLE_MARKERS = re.compile(
    r"DECISION REQUIRED|OWNER[- ]GATED|OWNER DECISION|ARCHITECTURAL DECISION|\bOWNER:",
    re.IGNORECASE,
)
_OPEN_STATUSES = frozenset({"open", "in_progress", "blocked"})


def is_unlabeled_marker_match(issue: Any) -> bool:
    """True if *issue* is open, not an epic, title-matches a legacy decision
    marker, and does NOT already carry the ``decision`` label.

    This is ``--check-unlabeled-markers``'s discovery predicate: it widens
    the checked set beyond labeled beads so the convention lint is
    non-vacuous against a queue that still has pre-convention, title-marker
    decision beads. Excludes epics for the same reason
    ``decision_review.py::_is_decision_bead`` does -- a container epic (e.g.
    "Owner Decision Desk...") is not itself a single decision the owner
    resolves in one step, even when its title happens to match.
    """
    if not isinstance(issue, dict):
        return False
    if issue.get("status") not in _OPEN_STATUSES:
        return False
    if issue.get("issue_type") == "epic":
        return False
    labels = issue.get("labels")
    if isinstance(labels, list) and DECISION_LABEL in labels:
        return False
    title = issue.get("title") or ""
    return bool(_DECISION_TITLE_MARKERS.search(title))


def select_issues_to_check(issues: list[Any], *, check_unlabeled_markers: bool) -> list[Any]:
    """Narrow a raw issue pool (a full beads export, an unfiltered `bd list`)
    down to what the convention lint should check.

    A no-op unless ``check_unlabeled_markers`` is set -- callers that already
    curated their own issue set (existing ``--issues-json-file`` fixtures,
    the default ``bd list --label decision`` discovery) get it back
    unfiltered, preserving the original "explicit input is checked as given"
    contract. With the flag set, keeps every already-labeled issue plus every
    :func:`is_unlabeled_marker_match` hit, dropping everything else (e.g. the
    other ~5,660 unrelated beads in a full export).
    """
    if not check_unlabeled_markers:
        return issues
    selected = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        labels = issue.get("labels")
        if isinstance(labels, list) and DECISION_LABEL in labels:
            selected.append(issue)
        elif is_unlabeled_marker_match(issue):
            selected.append(issue)
    return selected


@dataclass(frozen=True)
class LintResult:
    issue_id: str
    title: str
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def lint_issue(issue: Any) -> LintResult:
    """Check one issue dict against the decision-bead convention.

    Accepts any issue shape returned by `bd show`/`bd list --json` (or the
    JSONL export). Never raises on malformed input -- a wrong-shaped field
    is reported as a violation, not an exception.
    """
    if not isinstance(issue, dict):
        return LintResult(
            issue_id="<unknown>",
            title="",
            violations=["invalid issue data format (expected a dictionary)"],
        )

    issue_id = str(issue.get("id") or "<unknown>")
    title = str(issue.get("title") or "")
    violations: list[str] = []

    labels = issue.get("labels")
    if not isinstance(labels, list) or DECISION_LABEL not in labels:
        violations.append(f"missing '{DECISION_LABEL}' label")

    metadata = issue.get("metadata")
    decision_meta = metadata.get("decision") if isinstance(metadata, dict) else None
    if not isinstance(decision_meta, dict):
        violations.append(
            'missing metadata.decision (expected {"options": [...], "default": "..."})'
        )
        decision_meta = {}

    options = decision_meta.get("options")
    cleaned_options: list[str] = []
    if not isinstance(options, list) or not options:
        violations.append("metadata.decision.options must be a non-empty list")
    else:
        cleaned_options = [o for o in options if isinstance(o, str) and o.strip()]
        if len(cleaned_options) != len(options):
            violations.append("metadata.decision.options must contain only non-blank strings")
        if len(set(cleaned_options)) != len(cleaned_options):
            violations.append("metadata.decision.options must not contain duplicates")

    default = decision_meta.get("default")
    if not isinstance(default, str) or not default.strip():
        violations.append("metadata.decision.default must be a non-blank string")
    elif cleaned_options and default not in cleaned_options:
        violations.append(
            f"metadata.decision.default {default!r} must exactly match one entry in "
            "metadata.decision.options"
        )

    if not issue.get("due_at"):
        violations.append("due_at (deadline) must be set -- e.g. `bd update <id> --due 2026-07-25`")

    return LintResult(issue_id=issue_id, title=title, violations=violations)


def lint_issues(issues: list[Any]) -> list[LintResult]:
    return [lint_issue(issue) for issue in issues]


class BdUnavailableError(RuntimeError):
    """Raised when live issue data cannot be obtained from `bd`."""


def _run_bd_json(args: list[str]) -> Any:
    try:
        proc = subprocess.run(["bd", *args, "--json"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise BdUnavailableError(f"`bd` not found on PATH: {exc}") from exc

    if proc.returncode != 0:
        raise BdUnavailableError(
            f"`bd {' '.join(args)} --json` failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BdUnavailableError(
            f"`bd {' '.join(args)} --json` returned non-JSON output: {exc}"
        ) from exc


def load_issues_from_bd(
    issue_ids: list[str], *, status: str, check_unlabeled_markers: bool = False
) -> list[dict[str, Any]]:
    """Fetch issue records live via the `bd` CLI.

    With explicit IDs, uses `bd show` (returns exactly those issues,
    regardless of label, so an ID missing the `decision` label still shows
    up as a violation rather than being silently skipped). Without IDs,
    discovers via `bd list --label decision` scoped to `status` -- unless
    `check_unlabeled_markers` is set, in which case discovery drops the
    `--label` filter (fetches every issue at `status`) so
    `select_issues_to_check` has unlabeled, marker-matched beads to find.
    """
    if issue_ids:
        data = _run_bd_json(["show", *issue_ids])
    elif check_unlabeled_markers:
        data = _run_bd_json(["list", "--status", status])
    else:
        data = _run_bd_json(["list", "--label", DECISION_LABEL, "--status", status])

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise BdUnavailableError(f"unexpected `bd` JSON shape: {type(data).__name__}")
    return data


def load_issues_from_file(path: Path) -> list[dict[str, Any]]:
    """Load issues from *path* -- accepts either a single JSON document (a
    list of issues, or one issue object) or newline-delimited JSON (one
    issue object per line).

    bu-hmdqz.6: the weekly decision_review job feeds this straight from the
    mounted ``issues.export.jsonl`` file, which is ``bd export``'s own
    format -- JSONL, not a single JSON array (`bd export --help`: "Each line
    is a complete JSON object"). Without this fallback, that real input
    would raise ``json.JSONDecodeError: Extra data`` on the second line and
    the weekly lint check would silently degrade to "could not obtain issue
    data" every single run. Tries the single-document parse first so
    existing offline fixtures (a plain JSON array/object) are unaffected.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BdUnavailableError(f"failed to read or parse {path}: {exc}") from exc

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        records: list[Any] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise BdUnavailableError(f"failed to read or parse {path}: {exc}") from exc
        if not records:
            raise BdUnavailableError(f"failed to read or parse {path}: no JSON records found")
        data = records

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise BdUnavailableError(f"unexpected JSON shape in {path}: {type(data).__name__}")
    return data


def format_results(results: list[LintResult]) -> str:
    failing = [r for r in results if not r.ok]
    if not failing:
        return f"lint_decision_beads: clean -- {len(results)} decision bead(s) checked."
    lines = [
        f"lint_decision_beads: {len(failing)}/{len(results)} decision bead(s) fail the convention:"
    ]
    for r in failing:
        lines.append(f"  {r.issue_id} ({r.title!r}):")
        for v in r.violations:
            lines.append(f"    - {v}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lint decision beads for structured options/default/deadline."
    )
    parser.add_argument(
        "issue_ids", nargs="*", help="Specific issue IDs to lint (default: discover via label)"
    )
    parser.add_argument(
        "--status",
        default="open",
        help="Status filter for discovery mode (default: open; use 'all' for all)",
    )
    parser.add_argument(
        "--issues-json-file",
        type=Path,
        help="Lint issues from a JSON file instead of live `bd` (offline/CI-safe input)",
    )
    parser.add_argument(
        "--check-unlabeled-markers",
        action="store_true",
        help=(
            "Non-vacuous discovery mode: also check open, non-epic beads whose titles "
            "match a legacy decision marker (DECISION REQUIRED / OWNER-GATED / OWNER "
            "DECISION / ARCHITECTURAL DECISION / OWNER:) but lack the 'decision' label -- "
            "they then fail the existing 'missing label' check. Only widens discovery "
            "(bd list without --label, or the full --issues-json-file pool); explicit "
            "issue IDs are always checked as given, unaffected by this flag."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.issues_json_file:
            issues = load_issues_from_file(args.issues_json_file)
        else:
            issues = load_issues_from_bd(
                args.issue_ids,
                status=args.status,
                check_unlabeled_markers=args.check_unlabeled_markers,
            )
    except BdUnavailableError as exc:
        print(f"lint_decision_beads: could not obtain issue data: {exc}", file=sys.stderr)
        return 2

    if not args.issue_ids:
        issues = select_issues_to_check(
            issues, check_unlabeled_markers=args.check_unlabeled_markers
        )

    results = lint_issues(issues)

    if args.json:
        json.dump(
            [
                {"id": r.issue_id, "title": r.title, "ok": r.ok, "violations": r.violations}
                for r in results
            ],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(format_results(results))

    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
