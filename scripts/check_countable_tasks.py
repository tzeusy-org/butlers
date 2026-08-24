#!/usr/bin/env python3
"""
check_countable_tasks.py

Fail when an unarchived OpenSpec change's ``tasks.md`` carries no task line the
``openspec archive`` completion gate can count.

Why this exists
---------------
``openspec archive`` prints ``Task status: ...`` and, when tasks remain
unchecked, warns (or blocks without ``--yes``) before archiving. That gate reads
one thing only: markdown checkbox lines. OpenSpec 1.9.0's
``TASK_LINE_PATTERN`` (``dist/utils/task-progress.js``) is::

    /^\\s*[-*]\\s*\\[([\\sxX])\\]\\s*(.*)/

applied per line, with ``x``/``X`` meaning done. A ``tasks.md`` written as
``### N.`` sections with per-task acceptance bullets -- richer than most
checkbox files in this repo, not lazier -- matches that pattern zero times, so
the gate reports ``Task status: No tasks``, cannot be incomplete, and archives
unprompted (bu-h7igs, measured on ``commitment-lifecycle``).

The failure is not a missing warning. It is that the gate's silence is
indistinguishable from its pass: "archived with no task warning" reads as
evidence the tasks were done. This gate restores the difference by refusing to
let a change reach archive in a form the completion gate cannot see.

An absent ``tasks.md`` fails for the same reason: ``getTaskProgressDetailForChange``
falls back to a single top-level ``tasks.md`` and treats ENOENT as zero tasks,
so the change archives with the same unearned green. Five archived changes went
in that way.

Exemptions
----------
``scripts/countable-tasks-exemptions.json`` maps a change directory name to a
one-line reason. It is deliberately not a ratchet: an entry is a human saying
"this change genuinely tracks no tasks", not frozen debt, and there is no
``--update`` flag to write one automatically.

Limits
------
This checks that the gate can *see* tasks, not that the tasks are true. A file
of unchecked boxes passes here and is exactly what it claims: unproven work that
``openspec archive`` will warn about. A file of checked boxes passes here too,
and this script has no way to know whether any of it happened.

Task-file resolution mirrors the ``spec-driven`` schema shipped with OpenSpec
1.9.0, whose tracked-tasks artifact generates a single top-level ``tasks.md``
(``schemas/spec-driven/schema.yaml``: ``apply.tracks: tasks.md``). A schema whose
tracked artifact globbed nested task files would need this resolution widened.

Usage:
  python3 scripts/check_countable_tasks.py                    # gate
  python3 scripts/check_countable_tasks.py --root <dir>       # scan another tree
  python3 scripts/check_countable_tasks.py --include-archived # also report archived changes

Exit codes:
  0  Every unarchived change's tasks.md has at least one countable task line.
  1  At least one change would archive reporting `Task status: No tasks`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXEMPTIONS_PATH = Path(__file__).resolve().parent / "countable-tasks-exemptions.json"

# Transliteration of OpenSpec 1.9.0's TASK_LINE_PATTERN. Kept character-for-
# character equivalent on purpose: every widening here would count a line the
# real gate ignores, and every narrowing would pass a change the real gate
# cannot see. Python's `\s` and JavaScript's differ on `﻿` only (JS counts
# it as whitespace, Python does not), which affects a BOM-prefixed first task
# line and nothing else.
TASK_LINE_PATTERN = re.compile(r"^\s*[-*]\s*\[([\sxX])\]\s*(.*)")


@dataclass(frozen=True)
class Progress:
    """What `openspec archive` would report for one change."""

    total: int
    completed: int

    def status(self) -> str:
        """OpenSpec's `formatTaskStatus`, verbatim."""
        if self.total == 0:
            return "No tasks"
        if self.completed == self.total:
            return "✓ Complete"
        return f"{self.completed}/{self.total} tasks"


def count_tasks(content: str) -> Progress:
    """Count the task lines OpenSpec's parser would find, in document order.

    Every matching line counts wherever it sits -- inside a code fence, an HTML
    comment, or an indented block -- because that is what OpenSpec does, and a
    guard that disagreed with the gate it models would be worse than none.
    """
    total = 0
    completed = 0
    for line in content.split("\n"):
        match = TASK_LINE_PATTERN.match(line)
        if match:
            total += 1
            if match.group(1).lower() == "x":
                completed += 1
    return Progress(total=total, completed=completed)


def progress_for_change(change_dir: Path) -> Progress:
    """Task progress for one change, or zero tasks when `tasks.md` is absent."""
    tasks_file = change_dir / "tasks.md"
    if not tasks_file.is_file():
        return Progress(total=0, completed=0)
    return count_tasks(tasks_file.read_text("utf-8"))


def change_dirs(changes_dir: Path, *, archived: bool) -> list[Path]:
    root = changes_dir / "archive" if archived else changes_dir
    if not root.is_dir():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and p.name != "archive"]


def invisible_changes(changes_dir: Path, *, archived: bool = False) -> list[tuple[str, str]]:
    """``(change name, why)`` for every change the archive gate would see as taskless."""
    found: list[tuple[str, str]] = []
    for change_dir in change_dirs(changes_dir, archived=archived):
        if progress_for_change(change_dir).total > 0:
            continue
        reason = (
            "no tasks.md"
            if not (change_dir / "tasks.md").is_file()
            else "tasks.md has no `- [ ]` task line"
        )
        found.append((change_dir.name, reason))
    return found


def load_exemptions(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text("utf-8"))
    return {str(name): str(reason) for name, reason in raw.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when an unarchived change's tasks.md would make `openspec archive` "
            "report `Task status: No tasks`."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Tree containing openspec/ to scan (default: repo root).",
    )
    parser.add_argument(
        "--exemptions",
        type=Path,
        default=EXEMPTIONS_PATH,
        help="JSON map of change name -> reason for tracking no tasks (default: %(default)s).",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Also report archived changes (informational; never affects the exit code).",
    )
    args = parser.parse_args()

    changes_dir = args.root / "openspec" / "changes"
    if not changes_dir.is_dir():
        print(f"No openspec/changes directory under {args.root}", file=sys.stderr)
        return 1

    exemptions = load_exemptions(args.exemptions)
    findings = invisible_changes(changes_dir)
    scanned = len(change_dirs(changes_dir, archived=False))

    if args.include_archived:
        archived = invisible_changes(changes_dir, archived=True)
        archived_total = len(change_dirs(changes_dir, archived=True))
        print(
            f"Archived corpus: {len(archived)} of {archived_total} change(s) archived "
            "reporting `Task status: No tasks`."
        )
        for name, reason in archived:
            print(f"  {name}: {reason}")

    unexempt = [(name, reason) for name, reason in findings if name not in exemptions]
    exempt = [name for name, _ in findings if name in exemptions]

    for name in exempt:
        print(f"note: {name} is exempt -- {exemptions[name]}")

    if unexempt:
        print(
            f"\n{len(unexempt)} change(s) would archive reporting `Task status: No tasks`, "
            "so the archive completion gate cannot fire on them:"
        )
        for name, reason in unexempt:
            print(f"  {name}: {reason}")
        print(
            "\nThe gate counts markdown checkbox lines only, so a `### N.` heading-style "
            "tasks.md archives silently and its silence reads as a pass. Give each task a "
            "`- [ ]` line (the heading and its acceptance bullets can stay), or record the "
            f"change in {args.exemptions} with a reason."
        )
        return 1

    print(
        f"All {scanned} unarchived change(s) carry task lines `openspec archive` can count"
        + (f" ({len(exempt)} exempt)." if exempt else ".")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
