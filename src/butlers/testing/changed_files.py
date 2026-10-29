"""Changed-file detection for scoped test planning.

The refinery still needs a branch-only comparison, while agents need a plan
that also sees their staged, unstaged, and untracked worktree changes.  Keep
those inputs separate: a merge-queue plan must be reproducible from refs, and
an iteration plan must not omit code the agent has not committed yet.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

UNTRACKED_RESIDUE = frozenset({".beads.gate.lock", ".claude/settings.local.json"})
HISTORICAL_MANIFESTS = frozenset(
    f".github/ci-test-shards/{lane}-{shard}.txt"
    for lane in ("unit", "integration")
    for shard in range(1, 6)
)


def git_environment() -> dict[str, str]:
    """Read the specified checkout, never an inherited alternate Git index/tree."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


@dataclass(frozen=True)
class ChangedFiles:
    """Result of diff-based changed-file detection."""

    files: list[str] = field(default_factory=list)
    base_ref: str = ""
    head_ref: str = ""
    sources: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    source_head: str = ""
    base_head: str = ""
    statuses: tuple[tuple[str, str, str], ...] = ()
    added_files: tuple[str, ...] = ()


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
        timeout=30,
        env=git_environment(),
    )
    if result.returncode != 0:
        # Ref names, remote diagnostics and local paths may contain private data.
        raise RuntimeError(f"Git {purpose} unavailable (exit {result.returncode})") from None
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    ]


def _commit(ref: str, repo_dir: str | Path | None) -> str:
    values = _run_git(
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        repo_dir=repo_dir,
        purpose="commit identity",
    )
    value = values[0].strip() if len(values) == 1 else ""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise RuntimeError("Git commit identity unavailable")
    return value


def _changes(args: list[str], source: str, repo_dir: str | Path | None):
    """Parse status/path pairs without line splitting or rename ambiguity."""
    fields = _run_git(args, repo_dir=repo_dir, purpose="changed paths")
    if len(fields) % 2:
        raise RuntimeError("Git changed-path representation unavailable")
    pairs = list(zip(fields[::2], fields[1::2], strict=True))
    if any(status not in {"A", "M", "D", "T", "U", "X", "B"} for status, _ in pairs):
        raise RuntimeError("Git changed-path status unavailable")
    return [(source, status, path) for status, path in pairs]


def _tree(root: Path, commit: str) -> dict[str, tuple[str, str]]:
    result = {}
    for row in _run_git(["ls-tree", "-r", "-z", commit], repo_dir=root, purpose="tree objects"):
        metadata, path = row.split("\t", 1)
        mode, kind, oid = metadata.split(" ")
        if kind == "blob":
            result[path] = (mode, oid)
    return result


def _blob(root: Path, oid: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", oid],
        cwd=root,
        capture_output=True,
        timeout=30,
        check=False,
        env=git_environment(),
    )
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("historical source blob unavailable")
    return result.stdout


def historical_manifest_delta(root: Path, changed: ChangedFiles) -> dict:
    """Verify immutable manifest/test changes, never install an old manifest.

    This proves bounded source syntax/physical ownership only. Actual pytest
    lane/marker membership, current readers, cost and execution remain separate
    authorities; eligibility alone must never produce an admitted SCOPED mode.
    """
    prefix = ".github/ci-test-shards/"
    refused = {"eligible": False, "reason": "MANIFEST_INELIGIBLE"}
    manifest_changes = sorted(p for p in changed.files if p.startswith(prefix))
    if not manifest_changes:
        return refused
    try:
        if (
            changed.sources != ("branch",)
            or not changed.source_head
            or not changed.base_head
            or any(p not in HISTORICAL_MANIFESTS for p in manifest_changes)
        ):
            return refused
        actual = get_changed_files(changed.source_head, changed.base_head, repo_dir=root)
        if actual.files != sorted(changed.files) or actual.base_head != changed.base_head:
            return refused
        trees = {"base": _tree(root, changed.base_head), "head": _tree(root, changed.source_head)}
        snapshots, bindings = {}, []
        for side, tree in trees.items():
            parsed, owners = {}, {}
            for path, (mode, oid) in sorted(tree.items()):
                if not path.startswith(prefix) or not path.endswith(".txt"):
                    continue
                if mode != "100644" or path not in HISTORICAL_MANIFESTS:
                    return refused
                body = _blob(root, oid)
                entries, headers = [], []
                for line in body.decode("utf-8", "strict").splitlines():
                    if not line or line.startswith("#"):
                        headers.append(line)
                        continue
                    parts = Path(line).parts
                    if (
                        line != Path(line).as_posix()
                        or ".." in parts
                        or "\\" in line
                        or any(ord(c) < 32 for c in line)
                        or not line.startswith(("tests/", "roster/"))
                        or not Path(line).name.startswith("test_")
                        or not line.endswith(".py")
                        or line not in tree
                        or tree[line][0] not in {"100644", "100755"}
                    ):
                        return refused
                    lane = Path(path).name.split("-", 1)[0]
                    key = (lane, line)
                    if key in owners:
                        return refused
                    owners[key] = path
                    entries.append(line)
                parsed[path] = (entries, headers)
                bindings.append(
                    {
                        "side": side,
                        "path": path,
                        "blob": oid,
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
            snapshots[side] = (parsed, owners)
        base, old_owners = snapshots["base"]
        head, new_owners = snapshots["head"]
        if set(base) != HISTORICAL_MANIFESTS or set(head) != HISTORICAL_MANIFESTS:
            return refused
        touched = set()
        for path in manifest_changes:
            old, old_headers = base[path]
            new, new_headers = head[path]
            common = set(old) & set(new)
            if (
                old_headers != new_headers
                or [p for p in old if p in common] != [p for p in new if p in common]
                or set(old) == set(new)
            ):
                return refused
            changed_entries = set(old) ^ set(new)
            if not changed_entries <= set(changed.files):
                return refused
            touched.update(changed_entries)
        # Moving an old file between same-lane shards is an assignment change,
        # even when an unrelated modification happens to touch that test file.
        if any(key in new_owners and new_owners[key] != owner for key, owner in old_owners.items()):
            return refused
        for path in touched:
            if path in trees["base"] and path in trees["head"]:
                before = ast.parse(_blob(root, trees["base"][path][1]))
                after = ast.parse(_blob(root, trees["head"][path][1]))

                def markers(tree):
                    return {
                        n.attr
                        for n in ast.walk(tree)
                        if isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Attribute)
                        and n.value.attr == "mark"
                        and isinstance(n.value.value, ast.Name)
                        and n.value.value.id == "pytest"
                    }

                if markers(before) == markers(after):
                    return refused  # Existing-file membership needs a real marker change.
        for path in changed.files:
            if not (
                path.endswith(".py")
                and Path(path).name.startswith("test_")
                and path.startswith(("tests/", "roster/"))
            ):
                continue
            if path in trees["head"] and not any(p == path for _, p in new_owners):
                return refused
            if path not in trees["head"] and any(p == path for _, p in new_owners):
                return refused
        return {
            "eligible": True,
            "reason": None,
            "base_head": changed.base_head,
            "source_head": changed.source_head,
            "manifest_paths": manifest_changes,
            "test_paths": sorted(touched),
            "bindings": bindings,
            "limit": "Source ownership only; not collected membership/cost/execution proof",
        }
    except (
        OSError,
        ValueError,
        KeyError,
        UnicodeError,
        SyntaxError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ):
        return refused


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
    head = _commit(branch, repo_dir)
    base_commit = _commit(base, repo_dir)
    fork = _run_git(["merge-base", base_commit, head], repo_dir=repo_dir, purpose="merge base")[
        0
    ].strip()
    statuses = _changes(
        ["diff", "--name-status", "-z", "--no-renames", fork, head, "--"],
        "branch",
        repo_dir,
    )
    return ChangedFiles(
        files=sorted({path for _, _, path in statuses}),
        base_ref=base,
        head_ref=branch,
        sources=("branch",),
        deleted_files=tuple(path for _, status, path in statuses if status == "D"),
        statuses=tuple(statuses),
        source_head=head,
        base_head=fork,
        added_files=tuple(path for _, status, path in statuses if status == "A"),
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

    committed = get_changed_files("HEAD", base, repo_dir=repo_dir)
    queries = (
        (
            "branch",
            [
                "diff",
                "--name-status",
                "-z",
                "--no-renames",
                committed.base_head,
                committed.source_head,
                "--",
            ],
            "branch diff",
        ),
        ("staged", ["diff", "--name-status", "-z", "--no-renames", "--cached", "--"], "index diff"),
        ("unstaged", ["diff", "--name-status", "-z", "--no-renames", "--"], "worktree diff"),
        ("untracked", ["ls-files", "--others", "--exclude-standard", "-z"], "untracked files"),
    )

    files: set[str] = set()
    untracked: list[str] = []
    statuses: list[tuple[str, str, str]] = []
    sources: list[str] = []
    for source, args, purpose in queries:
        if source == "untracked":
            result = _run_git(args, repo_dir=repo_dir, purpose=purpose)
        else:
            changes = _changes(args, source, repo_dir)
            statuses.extend(changes)
            result = [path for _, _, path in changes]
        if result:
            if source == "untracked":
                untracked = result
                result = [name for name in result if name not in UNTRACKED_RESIDUE]
            files.update(result)
            sources.append(source)

    return ChangedFiles(
        files=sorted(files),
        base_ref=base,
        head_ref="HEAD",
        sources=tuple(sources),
        deleted_files=tuple(sorted({path for _, status, path in statuses if status == "D"})),
        untracked_files=tuple(untracked),
        statuses=tuple(statuses),
        source_head=committed.source_head,
        base_head=committed.base_head,
        # Index status alone cannot prove novelty: a tracked base file can be
        # deleted and reintroduced locally. Consult the immutable fork tree.
        added_files=tuple(
            sorted(
                ({path for _, status, path in statuses if status == "A"} | set(untracked))
                - set(_tree(Path(repo_dir or "."), committed.base_head))
            )
        ),
    )
