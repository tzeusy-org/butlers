"""Scoped test planner for refinery and agent-worktree flows.

Combines changed-file detection (``changed_files``) with source-to-test mapping
(``source_test_map``).  The command-line interface is deliberately **plan
only**: it tells an agent what to run and when to escalate, but never turns a
guessed scope into passing-test evidence.

Usage from the refinery::

    from butlers.testing.scoped_runner import plan_scoped_tests, build_pytest_command

    plan = plan_scoped_tests("polecat/flint/bu-c05", base="origin/main")
    print(plan.report())
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from butlers.testing.changed_files import (
    ChangedFiles,
    get_changed_files,
    get_worktree_changed_files,
)
from butlers.testing.source_test_map import FULL_SUITE, configured_testpaths, resolve_test_paths

# Kept for the explicit legacy ``run_scoped_tests`` API.  A planned path is
# never silently ignored: DB and migration coverage is part of the selector's
# safety contract.
DEFAULT_IGNORES: list[str] = []
DEFAULT_EXTRA_ARGS: list[str] = ["-n", "auto"]

# ---------------------------------------------------------------------------
# Full-suite fallback allowlist
#
# When any changed file matches a pattern here, the full test suite runs
# regardless of what source_test_map would normally select.  Patterns ending
# with "/" are treated as path prefixes; all others are exact matches.
#
# This list is intentionally separate from source_test_map.FULL_SUITE_TRIGGERS
# so that the runner can apply coarser-grained shared-infrastructure rules
# with detailed per-file logging, and so it can be overridden per invocation.
# ---------------------------------------------------------------------------

FULL_SUITE_FALLBACK_ALLOWLIST: tuple[str, ...] = (
    "conftest.py",
    "Makefile",
    "uv.lock",
    ".github/",
    "alembic/",
    "migrations/",
    "src/butlers/core/",
    "src/butlers/db.py",
    "src/butlers/migrations/",
    "src/butlers/testing/",
    "src/butlers/modules/base.py",
    "src/butlers/modules/registry.py",
    "pyproject.toml",
)


@dataclass(frozen=True)
class ScopedTestPlan:
    """A suggested test scope, never a completed verification result."""

    scope: str  # "scoped", "full", "none"
    test_paths: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""
    sources: tuple[str, ...] = ()

    def report(self) -> str:
        """Human-readable report of the test plan."""
        lines: list[str] = []
        lines.append("[PLAN ONLY] pytest was not executed")
        if self.scope == "none":
            lines.append("[NO PYTEST SCOPE] " + self.reason)
        elif self.scope == "full":
            lines.append("[ESCALATE] " + self.reason)
            lines.append("  Suggested broad roots: " + ", ".join(self.test_paths))
        else:
            lines.append(f"[SCOPED] {self.reason}")
            lines.append(f"  Test paths: {', '.join(self.test_paths)}")

        if self.sources:
            lines.append("  Change sources: " + ", ".join(self.sources))
        if self.changed_files:
            lines.append(f"  Changed files ({len(self.changed_files)}):")
            for f in self.changed_files:
                lines.append(f"    - {f}")

        return "\n".join(lines)


def find_fallback_trigger(
    changed_files: list[str],
    allowlist: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST,
) -> tuple[str, str] | None:
    """Return ``(file, matched_pattern)`` if any file triggers the full-suite fallback.

    Patterns ending with ``"/"`` are matched as path prefixes; all others are
    exact matches.  Returns ``None`` if no file matches any allowlist pattern.
    """
    for f in changed_files:
        while f.startswith("./"):
            f = f[2:]
        for pattern in allowlist:
            if pattern.endswith("/"):
                if f.startswith(pattern) or f == pattern.rstrip("/"):
                    return (f, pattern)
            else:
                if f == pattern:
                    return (f, pattern)
    return None


def _full_suite(repo_dir: str | Path | None) -> list[str]:
    """Resolve the requested checkout's test roots, with a safe installed fallback."""

    try:
        return configured_testpaths(repo_dir)
    except (KeyError, OSError, tomllib.TOMLDecodeError):
        return list(FULL_SUITE)


def _normalise_existing_test_paths(
    test_paths: list[str],
    *,
    repo_dir: str | Path | None,
) -> tuple[list[str], list[str]]:
    """Keep emitted paths valid, broadening deleted test files to their parent.

    A deleted test is itself important input, but passing its vanished filename
    to pytest produces collection failure instead of useful coverage.  The
    nearest surviving directory is conservative and still lets a later
    ``--collect-only`` run prove the topology is sound.
    """

    root = Path(repo_dir or ".").resolve()
    normalised: list[str] = []
    notes: list[str] = []
    for test_path in test_paths:
        candidate = root / test_path
        if candidate.exists():
            resolved = test_path
        elif test_path.endswith(".py") and (
            test_path.startswith("tests/") or test_path.startswith("roster/")
        ):
            parent = Path(test_path).parent
            while parent != Path(".") and not (root / parent).exists():
                parent = parent.parent
            if parent == Path("."):
                return [], [f"No surviving parent scope for deleted test path {test_path!r}"]
            resolved = f"{parent.as_posix().rstrip('/')}/"
            notes.append(f"Deleted test path {test_path!r} widened to {resolved!r}")
        else:
            return [], [f"Planned path {test_path!r} does not exist"]

        if any(parent.endswith("/") and resolved.startswith(parent) for parent in normalised):
            continue
        if resolved.endswith("/"):
            normalised = [existing for existing in normalised if not existing.startswith(resolved)]
        if resolved not in normalised:
            normalised.append(resolved)
    return normalised, notes


def _plan_for_changed_files(
    changed: ChangedFiles,
    *,
    repo_dir: str | Path | None,
    fallback_allowlist: tuple[str, ...],
) -> ScopedTestPlan:
    """Build a plan from already-discovered paths."""

    full_suite = _full_suite(repo_dir)

    if not changed.files:
        return ScopedTestPlan(
            scope="none",
            reason="No branch, staged, unstaged, or untracked files changed",
            sources=changed.sources,
        )

    trigger = find_fallback_trigger(changed.files, fallback_allowlist)
    if trigger:
        file, pattern = trigger
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            reason=(
                f"Escalate: {file!r} matches shared-infrastructure pattern {pattern!r}; "
                "state the affected test lanes explicitly"
            ),
        )

    test_paths = resolve_test_paths(changed.files, repo_dir=repo_dir)

    if not test_paths:
        return ScopedTestPlan(
            scope="none",
            changed_files=changed.files,
            sources=changed.sources,
            reason="Known non-testable paths only; no pytest command was selected",
        )

    if test_paths == full_suite:
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            reason="Escalate: cross-cutting, migration, configuration, or unknown path detected",
        )

    existing_paths, notes = _normalise_existing_test_paths(test_paths, repo_dir=repo_dir)
    if not existing_paths:
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            reason="Escalate: " + "; ".join(notes),
        )

    reason = (
        f"Scoped to {len(existing_paths)} test path(s) from {len(changed.files)} changed file(s)"
    )
    if notes:
        reason += "; " + "; ".join(notes)
    return ScopedTestPlan(
        scope="scoped",
        test_paths=existing_paths,
        changed_files=changed.files,
        sources=changed.sources,
        reason=reason,
    )


def plan_scoped_tests(
    branch: str,
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
    fallback_allowlist: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST,
) -> ScopedTestPlan:
    """Determine which tests to run for an MR branch.

    Checks the *fallback_allowlist* first: if any changed file matches a
    shared-infrastructure pattern, the full suite runs immediately with a log
    message identifying the triggering file and pattern.  Otherwise delegates
    to ``resolve_test_paths`` for fine-grained scoping.
    """
    return _plan_for_changed_files(
        get_changed_files(branch, base, repo_dir=repo_dir),
        repo_dir=repo_dir,
        fallback_allowlist=fallback_allowlist,
    )


def plan_worktree_tests(
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
    fallback_allowlist: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST,
) -> ScopedTestPlan:
    """Plan tests for the current dirty worktree without executing pytest."""

    try:
        changed = get_worktree_changed_files(base, repo_dir=repo_dir)
    except RuntimeError as exc:
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            reason=f"Escalate: unable to compute worktree diff against {base!r}: {exc}",
        )
    return _plan_for_changed_files(
        changed,
        repo_dir=repo_dir,
        fallback_allowlist=fallback_allowlist,
    )


def build_pytest_command(
    plan: ScopedTestPlan,
    *,
    ignores: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the pytest command line from a scoped test plan.

    Raises ``ValueError`` if ``plan.scope`` is ``"none"``.
    """
    if plan.scope == "none":
        raise ValueError(f"No tests to run: {plan.reason}")

    if ignores is None:
        ignores = list(DEFAULT_IGNORES)

    cmd = ["uv", "run", "pytest"]
    cmd.extend(plan.test_paths)

    for ignore in ignores:
        cmd.extend(["--ignore", ignore])

    cmd.extend(DEFAULT_EXTRA_ARGS)

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def run_scoped_tests(
    branch: str,
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
    fallback_allowlist: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST,
    ignores: list[str] | None = None,
    extra_args: list[str] | None = None,
    log_file: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Plan and execute scoped tests for an explicit legacy refinery caller.

    Prints a report of what was selected and why, then runs pytest.
    Returns the completed process (exit code 0 = pass).
    """
    plan = plan_scoped_tests(branch, base, repo_dir=repo_dir, fallback_allowlist=fallback_allowlist)

    print(plan.report(), flush=True)

    if plan.scope == "none":
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="No tests to run\n", stderr=""
        )

    cmd = build_pytest_command(plan, ignores=ignores, extra_args=extra_args)
    print(f"Running: {' '.join(cmd)}", flush=True)

    result = subprocess.run(
        cmd,
        capture_output=bool(log_file),
        text=True,
        cwd=repo_dir,
        check=False,
    )

    if log_file:
        Path(log_file).write_text(result.stdout + result.stderr)

    return result


def main(argv: list[str] | None = None) -> int:
    """Print a plan for a branch or the current worktree without running pytest."""

    import argparse

    parser = argparse.ArgumentParser(description="Plan scoped tests without executing pytest")
    parser.add_argument(
        "branch",
        nargs="?",
        help="Optional MR branch; omit to inspect the current dirty worktree",
    )
    parser.add_argument("--base", default="origin/main", help="Base ref (default: origin/main)")
    parser.add_argument("--repo-dir", default=None, help="Repository directory")
    args = parser.parse_args(argv)

    plan = (
        plan_scoped_tests(args.branch, base=args.base, repo_dir=args.repo_dir)
        if args.branch
        else plan_worktree_tests(base=args.base, repo_dir=args.repo_dir)
    )
    print(plan.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
