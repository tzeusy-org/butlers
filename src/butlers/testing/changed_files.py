"""Diff-based changed-file detection for MR branches.

Given an MR branch name, determines the list of files changed relative to a
base ref (default ``origin/main``).  This is the input to source-to-test
mapping for scoped test runs in the refinery merge queue.
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
    cmd = [
        "git",
        "diff",
        "--name-only",
        "--no-renames",
        f"{base}...{branch}",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=repo_dir,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"git diff failed (exit {result.returncode}): {result.stderr.strip()}")

    files = [f for f in result.stdout.strip().splitlines() if f]
    return ChangedFiles(
        files=sorted(set(files)),
        base_ref=base,
        head_ref=branch,
    )
