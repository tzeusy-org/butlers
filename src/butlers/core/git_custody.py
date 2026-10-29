"""Shared nonblocking exclusion for trusted repository claims and cleanup."""

from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path


class CustodyUnavailable(RuntimeError):
    """Repository identity is absent or another cooperating operator owns it."""


def common_git_directory(root: Path) -> Path:
    marker = root.resolve() / ".git"
    if marker.is_dir():
        gitdir = marker
    elif marker.is_file():
        value = marker.read_text().strip()
        if not value.startswith("gitdir: "):
            raise CustodyUnavailable("repository identity unavailable")
        gitdir = (marker.parent / value.removeprefix("gitdir: ")).resolve()
    else:
        raise CustodyUnavailable("repository identity unavailable")
    common = gitdir / "commondir"
    return (gitdir / common.read_text().strip()).resolve() if common.is_file() else gitdir.resolve()


@contextlib.contextmanager
def branch_exclusion(root: Path):
    """Hold the same common-Git lock before creating or retiring a claimed ref.

    The trusted coordinator acquires it before canonical claim, dispatch,
    reassignment or terminal release and affected worktree preparation/removal.
    Native producers acquire it before branch/worktree mutation. Never nest it;
    busy acquisition refuses immediately without a canonical or Git side effect.
    It is cooperation, not isolation from same-identity actors or GitHub writers;
    live remote mutation still needs its independent expected-SHA lease.
    """
    with (common_git_directory(root) / "ci-branch-custody.lock").open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CustodyUnavailable("repository custody busy") from exc
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
