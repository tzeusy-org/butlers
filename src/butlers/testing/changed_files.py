"""Changed-file detection for scoped test planning.

The refinery still needs a branch-only comparison, while agents need a plan
that also sees their staged, unstaged, and untracked worktree changes.  Keep
those inputs separate: a merge-queue plan must be reproducible from refs, and
an iteration plan must not omit code the agent has not committed yet.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ChangedFiles:
    """Result of diff-based changed-file detection."""

    files: list[str] = field(default_factory=list)
    base_ref: str = ""
    head_ref: str = ""
    sources: tuple[str, ...] = ()


def _run_git(
    args: list[str],
    *,
    repo_dir: str | Path | None,
    purpose: str,
) -> list[str]:
    """Run a NUL-delimited Git file query and return its paths.

    Git paths may contain spaces, so newline splitting is not a safe parser for
    a planner that agents will rely on.  ``--no-renames`` deliberately reports
    a rename as an old-path deletion plus a new-path addition, allowing the
    caller to widen a deleted test to its surviving parent scope.
    """

    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=False,
        cwd=repo_dir,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {purpose} failed (exit {result.returncode}): {stderr}")
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    ]


def get_changed_files(
    branch: str,
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
) -> ChangedFiles:
    """Return files changed on *branch* relative to *base*.

    Uses three-dot diff (``base...branch``) so the comparison is against the
    merge-base — only changes introduced by the branch, not changes on *base*
    since the branch diverged.

    Renames are decomposed into delete + add (``--no-renames``) so both the old
    and new paths appear in the result.  This ensures tests associated with
    either path are selected by downstream mapping.

    Parameters
    ----------
    branch:
        The MR branch ref (e.g. ``polecat/flint/bu-c05``).
    base:
        The base ref to compare against.  Defaults to ``origin/main``.
    repo_dir:
        Working directory for the git command.  ``None`` uses cwd.

    Raises
    ------
    RuntimeError
        If the ``git diff`` command exits with a non-zero status.
    """
    return ChangedFiles(
        files=sorted(
            set(
                _run_git(
                    ["diff", "--name-only", "-z", "--no-renames", f"{base}...{branch}"],
                    repo_dir=repo_dir,
                    purpose="branch diff",
                )
            )
        ),
        base_ref=base,
        head_ref=branch,
        sources=("branch",),
    )


def get_worktree_changed_files(
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
) -> ChangedFiles:
    """Return branch, index, worktree, and untracked changes for an agent run.

    ``base...HEAD`` captures commits made on the current branch.  The remaining
    queries capture the three forms of unfinished agent work.  Their union is
    deliberately conservative: a path seen by more than one source appears
    once, and ignored untracked files remain excluded by Git itself.
    """

    queries = (
        (
            "branch",
            ["diff", "--name-only", "-z", "--no-renames", f"{base}...HEAD"],
            "branch diff",
        ),
        ("staged", ["diff", "--name-only", "-z", "--no-renames", "--cached"], "index diff"),
        ("unstaged", ["diff", "--name-only", "-z", "--no-renames"], "worktree diff"),
        ("untracked", ["ls-files", "--others", "--exclude-standard", "-z"], "untracked files"),
    )

    files: set[str] = set()
    sources: list[str] = []
    for source, args, purpose in queries:
        result = _run_git(args, repo_dir=repo_dir, purpose=purpose)
        if result:
            files.update(result)
            sources.append(source)

    return ChangedFiles(
        files=sorted(files),
        base_ref=base,
        head_ref="HEAD",
        sources=tuple(sources),
    )
