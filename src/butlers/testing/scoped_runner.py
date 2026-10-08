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

import json
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from butlers.testing.changed_files import (
    ChangedFiles,
    get_changed_files,
    get_worktree_changed_files,
)
from butlers.testing.manifest_scope import eligible as eligible_manifests
from butlers.testing.resource_readers import load as load_readers
from butlers.testing.resource_readers import resource_family, safe_path
from butlers.testing.scope_cost import predict
from butlers.testing.source_test_map import (
    FULL_SUITE,
    FULL_SUITE_FALLBACK_ALLOWLIST,
    configured_testpaths,
    resolve_test_paths,
)

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
# The catalog is shared with source_test_map; callers may add stricter boundaries.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopedTestPlan:
    """A suggested test scope, never a completed verification result."""

    scope: str  # "scoped", "full", "none"
    test_paths: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""
    sources: tuple[str, ...] = ()
    schema: str = "scoped-plan.v1"
    source_head: str | None = None
    base_head: str | None = None
    backend_applicable: bool = False
    reason_codes: tuple[str, ...] = ()
    reason_details: dict[str, list[str]] = field(default_factory=dict)
    reader_registry_digest: str | None = None
    cost_profile_digest: str | None = None
    predicted_seconds: float | None = None
    ceiling_seconds: float | None = None
    prediction_state: str = "unknown"
    ignored_residue: tuple[str, ...] = ()

    def data(self) -> dict:
        """Closed-reason source decision, never a test/elapsed result."""
        return {**asdict(self), "mode": self.scope, "changed_sources": self.sources}

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
    changed_files: list[str] | None = None,
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
        elif (
            test_path in (changed_files or [])
            and test_path.endswith(".py")
            and (test_path.startswith("tests/") or test_path.startswith("roster/"))
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

    root = Path(repo_dir or ".").resolve()
    full_suite = _full_suite(root)
    details: dict[str, list[str]] = {}

    def reject(code: str, path: str = "") -> None:
        details.setdefault(code, []).append(path)

    def ref(value: str) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", value + "^{commit}"],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
        return result.stdout.decode().strip() if result.returncode == 0 else None

    source_head, base_head = ref(changed.head_ref or "HEAD"), ref(changed.base_ref or "HEAD")
    if source_head is None or base_head is None:
        reject("BASE_UNAVAILABLE")
    if any(not safe_path(path) for path in changed.files):
        reject("UNSAFE_DIFF")
    try:
        manifests = eligible_manifests(root, changed.files, changed.base_ref, changed.head_ref)
    except ValueError:
        manifests = set()
        reject("MANIFEST_INELIGIBLE")
    files = [path for path in changed.files if path not in manifests]
    registry_digest = None
    if any(resource_family(path) for path in files):
        try:
            registry_digest = load_readers(root)["digest"]
        except ValueError as exc:
            reject(str(exc))  # These exceptions carry only closed source enums.
    for path in files:
        trigger = find_fallback_trigger([path], fallback_allowlist)
        if trigger:
            code = (
                "MIGRATION_BOUNDARY"
                if (
                    "migrations/" in path
                    or path.startswith("alembic/")
                    or path.endswith("contacts/backfill.py")
                )
                else "SHARED_INFRASTRUCTURE"
            )
            reject(code, path)
    connectors = {
        Path(path).stem.split("_")[0]
        for path in files
        if path.startswith("src/butlers/connectors/")
    }
    if len(connectors) > 1:
        reject("MULTI_CONNECTOR_BOUNDARY")
    test_paths = resolve_test_paths(files, repo_dir=root) if files else []
    if test_paths == full_suite and not details:
        reject("UNKNOWN_SOURCE")
    # A missing static mapping is not allowed to masquerade as a deleted test.
    from butlers.testing.source_test_map import _PREFIX_MAP

    for path in files:
        if path.startswith("src/"):
            for prefix, targets in _PREFIX_MAP:
                if path.startswith(prefix):
                    for target in targets:
                        if not (root / target).exists():
                            reject("STATIC_TARGET_MISSING", target)
                    break
    # Actual roster router consumers include factory-loaded APIs, not just
    # direct imports. Unknown loader semantics widen to the audited API family.
    for path in files:
        if path.startswith("roster/") and "/api/" in path:
            butler = path.split("/")[1]
            candidates = []
            for file in sorted((root / "tests/api").rglob("test_*.py")):
                body = file.read_text()
                if butler in body or "create_app" in body or "spec_from_file_location" in body:
                    candidates.append(str(file.relative_to(root)))
            if not candidates:
                reject("READER_UNCLASSIFIED", path)
            elif test_paths != full_suite:
                test_paths = sorted(set(test_paths) | set(candidates))
    existing_paths, notes = _normalise_existing_test_paths(
        test_paths, repo_dir=root, changed_files=changed.files
    )
    if test_paths and not existing_paths:
        reject("STATIC_TARGET_MISSING")
    if any(path in full_suite for path in existing_paths):
        reject("ROOT_WIDENING")
    if (
        any(path.startswith("tests/e2e/") for path in existing_paths)
        and "tests/e2e/" in fallback_allowlist
    ):
        reject("E2E_BOUNDARY")
    cost = (
        predict(root, existing_paths)
        if existing_paths and not details
        else {
            "prediction_state": "unknown",
            "predicted_seconds": None,
            "ceiling_seconds": None,
            "cost_profile_digest": None,
            "reason": None,
        }
    )
    if cost["reason"]:
        reject(cost["reason"])
    mode = "full" if details else "scoped" if existing_paths else "none"
    reason = (
        "Escalate: " + ", ".join(sorted(details))
        if details
        else (
            f"Scoped to {len(existing_paths)} test path(s) "
            f"from {len(changed.files)} changed file(s)"
            if existing_paths
            else "Known non-testable paths only; no pytest command was selected"
        )
    )
    if details:
        matches = [
            f"{path!r} matches {pattern!r}"
            for path in files
            if (found := find_fallback_trigger([path], fallback_allowlist))
            for pattern in [found[1]]
        ]
        if matches:
            reason += "; " + "; ".join(matches)
    if notes:
        reason += "; " + "; ".join(notes)
    return ScopedTestPlan(
        scope=mode,
        test_paths=full_suite if mode == "full" else existing_paths,
        changed_files=changed.files,
        sources=changed.sources,
        ignored_residue=changed.ignored_residue,
        reason=reason,
        source_head=source_head,
        base_head=base_head,
        backend_applicable=mode != "none",
        reason_codes=tuple(sorted(details)),
        reason_details={k: sorted(set(v)) for k, v in sorted(details.items())},
        reader_registry_digest=registry_digest,
        **{
            key: cost[key]
            for key in (
                "prediction_state",
                "predicted_seconds",
                "ceiling_seconds",
                "cost_profile_digest",
            )
        },
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
    try:
        changed = get_changed_files(branch, base, repo_dir=repo_dir)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            backend_applicable=True,
            reason="Escalate: BASE_UNAVAILABLE",
            reason_codes=("BASE_UNAVAILABLE",),
        )
    return _plan_for_changed_files(
        changed,
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
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            reason="Escalate: unable to compute worktree diff; BASE_UNAVAILABLE",
            reason_codes=("BASE_UNAVAILABLE",),
            backend_applicable=True,
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

    Raises ``ValueError`` for NONE or FULL; broad execution has an explicit lane.
    """
    if plan.scope == "full":
        raise ValueError("FULL requires the explicit CI-shaped broad verification lane")
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
    print(json.dumps(plan.data(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
