"""Scoped test runner for refinery merge-queue flow.

Combines diff-based changed-file detection (``changed_files``) with
source-to-test mapping (``source_test_map``) to run only the relevant test
subset for an MR branch.

Usage from the refinery::

    from butlers.testing.scoped_runner import plan_scoped_tests, build_pytest_command

    plan = plan_scoped_tests("polecat/flint/bu-c05", base="origin/main")
    print(plan.report())
    if plan.scope != "none":
        cmd = build_pytest_command(plan, extra_args=["-q", "--tb=short"])
        subprocess.run(cmd, check=True)
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from butlers.testing.changed_files import get_changed_files
from butlers.testing.source_test_map import FULL_SUITE, resolve_test_paths

DEFAULT_IGNORES: list[str] = ["tests/test_db.py", "tests/test_migrations.py"]

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
    "tests/conftest.py",
    "src/butlers/core/",
    "src/butlers/modules/base.py",
    "src/butlers/modules/registry.py",
    "pyproject.toml",
    "migrations/",
)


@dataclass(frozen=True)
class ScopedTestPlan:
    """Plan for a scoped test run."""

    scope: str  # "scoped", "full", "none"
    test_paths: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""

    def report(self) -> str:
        """Human-readable report of the test plan."""
        lines: list[str] = []
        if self.scope == "none":
            lines.append("[NO TESTS] " + self.reason)
        elif self.scope == "full":
            lines.append("[FULL SUITE] " + self.reason)
        else:
            lines.append(f"[SCOPED] {self.reason}")
            lines.append(f"  Test paths: {', '.join(self.test_paths)}")

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
        f = f.lstrip("./")
        for pattern in allowlist:
            if pattern.endswith("/"):
                if f.startswith(pattern) or f == pattern.rstrip("/"):
                    return (f, pattern)
            else:
                if f == pattern:
                    return (f, pattern)
    return None


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
    changed = get_changed_files(branch, base, repo_dir=repo_dir)

    if not changed.files:
        return ScopedTestPlan(scope="none", reason="No files changed")

    # Check fallback allowlist before fine-grained mapping so that
    # shared-infrastructure changes always get a clear, specific log message.
    trigger = find_fallback_trigger(changed.files, fallback_allowlist)
    if trigger:
        file, pattern = trigger
        reason = (
            f"Full-suite fallback triggered by {file!r} "
            f"(matches shared-infrastructure pattern {pattern!r})"
        )
        return ScopedTestPlan(
            scope="full",
            test_paths=list(FULL_SUITE),
            changed_files=changed.files,
            reason=reason,
        )

    test_paths = resolve_test_paths(changed.files)

    if not test_paths:
        return ScopedTestPlan(
            scope="none",
            changed_files=changed.files,
            reason="Changed files don't map to any tests",
        )

    if test_paths == FULL_SUITE:
        return ScopedTestPlan(
            scope="full",
            test_paths=test_paths,
            changed_files=changed.files,
            reason="Cross-cutting change detected — running full suite",
        )

    return ScopedTestPlan(
        scope="scoped",
        test_paths=test_paths,
        changed_files=changed.files,
        reason=(
            f"Scoped to {len(test_paths)} test path(s) from {len(changed.files)} changed file(s)"
        ),
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
    """Plan and execute scoped tests for an MR branch.

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run scoped tests for an MR branch")
    parser.add_argument("branch", help="MR branch name")
    parser.add_argument("--base", default="origin/main", help="Base ref (default: origin/main)")
    parser.add_argument("--repo-dir", default=None, help="Repository directory")
    parser.add_argument("--log-file", default=None, help="Write test output to file")
    parser.add_argument("--ignore", action="append", default=None, help="Test paths to ignore")
    parser.add_argument("extra", nargs="*", help="Extra pytest arguments")
    args = parser.parse_args()

    result = run_scoped_tests(
        args.branch,
        base=args.base,
        repo_dir=args.repo_dir,
        ignores=args.ignore,
        extra_args=args.extra if args.extra else None,
        log_file=args.log_file,
    )
    sys.exit(result.returncode)
