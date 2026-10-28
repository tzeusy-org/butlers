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

import fnmatch
import json
import math
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from butlers.testing import resource_readers, scope_cost
from butlers.testing.changed_files import (
    UNTRACKED_RESIDUE,
    ChangedFiles,
    get_changed_files,
    get_worktree_changed_files,
    historical_manifest_delta,
)
from butlers.testing.source_test_map import (
    FULL_SUITE,
    FULL_SUITE_PREFIX_TRIGGERS,
    FULL_SUITE_TRIGGERS,
    _resolve_test_paths,
    configured_testpaths,
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
# One source-owned catalog; callers may add stricter runner boundaries.
# ---------------------------------------------------------------------------

FULL_SUITE_FALLBACK_ALLOWLIST = tuple(sorted(FULL_SUITE_TRIGGERS)) + FULL_SUITE_PREFIX_TRIGGERS


@dataclass(frozen=True)
class ScopedTestPlan:
    """Candidate ownership plus one authoritative feedback admission mode.

    `scope` and `test_paths` retain the old library's ownership hints. Consumers
    authorizing execution must use `mode` / `as_dict`, including cost admission.
    Neither is evidence that a test actually executed.
    """

    scope: str  # "scoped", "full", "none"
    test_paths: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""
    sources: tuple[str, ...] = ()
    source_head: str = ""
    base_head: str = ""
    reason_codes: tuple[str, ...] = ()
    reason_details: tuple[tuple[str, str], ...] = ()
    reader_registry_digest: str | None = None
    selected_files: tuple[str, ...] = ()
    historical_manifest_witness: dict | None = None
    omitted_untracked_files: tuple[str, ...] = ()
    prediction: dict = field(
        default_factory=lambda: {
            "state": "unknown",
            "reason": "COST_UNKNOWN",
            "predicted_seconds": None,
            "ceiling_seconds": None,
            "profile_digest": None,
        }
    )

    @property
    def mode(self) -> str:
        if self.scope == "none":
            return "docs"
        if self.scope != "scoped" or self.reason_codes or self.prediction.get("reason"):
            return "full"
        if self.prediction.get("state") not in {"measured", "provisional"}:
            return "full"
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_head) or not re.fullmatch(
            r"[0-9a-f]{40}", self.base_head
        ):
            return "full"
        estimate = self.prediction.get("predicted_seconds")
        ceiling = self.prediction.get("ceiling_seconds")
        if (
            any(type(n) not in {int, float} or not math.isfinite(n) for n in (estimate, ceiling))
            or not 0 <= estimate <= ceiling <= 300
            or ceiling == 0
            or not isinstance(self.prediction.get("profile_digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.prediction["profile_digest"])
        ):
            return "full"
        return "scoped"

    @property
    def backend_applicable(self) -> bool:
        return self.scope != "none"

    def as_dict(self) -> dict:
        codes = set(self.reason_codes)
        if self.backend_applicable and self.prediction.get("reason"):
            codes.add(self.prediction["reason"])
        return {
            "schema": "pr-test-plan.v1",
            "source_head": self.source_head,
            "base_head": self.base_head,
            "changed_sources": list(self.sources),
            "changed_files": self.changed_files,
            "mode": self.mode,
            "backend_applicable": self.backend_applicable,
            "test_paths": list(self.selected_files or self.test_paths)
            if self.mode == "scoped"
            else [],
            "candidate_test_paths": self.test_paths,
            "reason_codes": sorted(codes),
            "reason_details": [{"code": code, "path": path} for code, path in self.reason_details],
            "reader_registry_digest": self.reader_registry_digest,
            "cost_profile_digest": self.prediction.get("profile_digest"),
            "predicted_seconds": self.prediction.get("predicted_seconds"),
            "ceiling_seconds": self.prediction.get("ceiling_seconds"),
            "prediction_state": self.prediction.get("state", "unknown"),
            "cost_context_provenance": self.prediction.get("context_provenance"),
            "historical_manifest_witness": self.historical_manifest_witness,
            "omitted_untracked_files": list(self.omitted_untracked_files),
        }

    def report(self) -> str:
        """Human-readable report of the test plan."""
        lines: list[str] = []
        lines.append("[PLAN ONLY] pytest was not executed")
        if self.scope == "none":
            lines.append("[NO PYTEST SCOPE] " + self.reason)
        elif self.mode == "full":
            lines.append("[ESCALATE] " + self.reason)
            lines.append("  Reasons: " + ", ".join(self.as_dict()["reason_codes"]))
        else:
            lines.append(f"[SCOPED] {self.reason}")
            lines.append(f"  Test paths: {', '.join(self.test_paths)}")

        if self.sources:
            lines.append("  Change sources: " + ", ".join(self.sources))
        if self.changed_files:
            lines.append(f"  Changed files ({len(self.changed_files)}):")
            for f in self.changed_files:
                lines.append(f"    - {f!r}")

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
    except (KeyError, OSError, ValueError, TypeError, tomllib.TOMLDecodeError):
        return list(FULL_SUITE)


def _normalise_existing_test_paths(
    test_paths: list[str],
    *,
    repo_dir: str | Path | None,
    deleted_files: tuple[str, ...] = (),
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
            test_path in deleted_files
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
            return [], [f"STATIC_TARGET_MISSING: planned path {test_path!r} does not exist"]

        if any(parent.endswith("/") and resolved.startswith(parent) for parent in normalised):
            continue
        if resolved.endswith("/"):
            normalised = [existing for existing in normalised if not existing.startswith(resolved)]
        if resolved not in normalised:
            normalised.append(resolved)
    return normalised, notes


def selected_test_files(root: Path, paths: list[str]) -> list[str]:
    """Expand owned directories to files; this is not pytest membership proof."""
    config = tomllib.loads((root / "pyproject.toml").read_text())
    patterns = config["tool"]["pytest"]["ini_options"].get(
        "python_files", ["test_*.py", "*_test.py"]
    )
    if (
        not isinstance(patterns, list)
        or not patterns
        or any(not isinstance(p, str) for p in patterns)
    ):
        raise ValueError("test filename policy unavailable")
    selected = set()
    for path in paths:
        candidate = root / path
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError("selected source escapes checkout")
        files = candidate.rglob("*.py") if candidate.is_dir() else [candidate]
        for file in files:
            if file.is_symlink():
                raise ValueError("selected symlink unsupported")
            if file.is_file() and any(fnmatch.fnmatchcase(file.name, p) for p in patterns):
                selected.add(file.relative_to(root).as_posix())
    return sorted(selected)


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

    details: set[tuple[str, str]] = set()
    reader_registry = None
    manifest_witness = None
    mapping_files = changed.files
    if any(p.startswith(".github/ci-test-shards/") for p in changed.files):
        witness = historical_manifest_delta(Path(repo_dir or ".").resolve(), changed)
        if witness["eligible"]:
            manifest_witness = witness
            mapping_files = [p for p in changed.files if p not in witness["manifest_paths"]]
        else:
            details.add(("MANIFEST_INELIGIBLE", ".github/ci-test-shards/"))
    for path in mapping_files:
        if (
            Path(path).is_absolute()
            or "\\" in path
            or any(part in {"", ".."} for part in path.split("/"))
            or any(ord(c) < 32 for c in path)
        ):
            details.add(("UNSAFE_DIFF", path))
        for pattern in fallback_allowlist:
            if path == pattern.rstrip("/") or (pattern.endswith("/") and path.startswith(pattern)):
                code = (
                    "MIGRATION_BOUNDARY"
                    if "migration" in path
                    or path.startswith(("alembic/", "src/butlers/modules/contacts/backfill"))
                    else "SHARED_INFRASTRUCTURE"
                )
                if pattern == "tests/e2e/":
                    code = "E2E_BOUNDARY"
                details.add((code, path))
        if resource_readers.family(path):
            try:
                if reader_registry is None:
                    reader_registry = resource_readers.load(Path(repo_dir or ".").resolve())
                if resource_readers.unbounded(path, reader_registry):
                    details.add(("READER_UNCLASSIFIED", path))
            except (ValueError, OSError, SyntaxError, TypeError, KeyError):
                details.add(("READER_REGISTRY_STALE", path))
    for _, status, path in changed.statuses:
        if status in {"T", "U", "X", "B"}:
            details.add(("UNSAFE_DIFF", path))
    # These two old files were condensed across multiple consumers. Until the
    # complete behavior join is witnessed, naming a current directory is not
    # permission to narrow their source changes.
    for path in mapping_files:
        if path in {"src/butlers/credential_store.py", "src/butlers/google_credentials.py"}:
            details.add(("STATIC_OWNER_UNPROVED", path))
    connector_paths = [p for p in changed.files if p.startswith("src/butlers/connectors/")]
    connector_families = {
        p.split("/")[3].split(".")[0] for p in connector_paths if len(p.split("/")) > 3
    }
    if len(connector_families) > 1:
        details.update(("MULTI_CONNECTOR_BOUNDARY", p) for p in connector_paths)

    def paths_for(files: list[str]) -> list[str]:
        return _resolve_test_paths(files, repo_dir=repo_dir, reader_registry=reader_registry)

    for path in mapping_files:
        if (
            not resource_readers.family(path)
            and paths_for([path]) == full_suite
            and not any(p == path for _, p in details)
        ):
            details.add(("UNKNOWN_SOURCE", path))
        candidates = paths_for([path])
        if candidates != full_suite:
            for candidate in candidates:
                if (
                    not (Path(repo_dir or ".") / candidate).exists()
                    and candidate not in changed.deleted_files
                ):
                    details.add(("STATIC_TARGET_MISSING", candidate))

    trigger = find_fallback_trigger(mapping_files, fallback_allowlist)
    if trigger or details:
        file, pattern = trigger or ("changed paths", "unproved source boundary")
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            source_head=changed.source_head,
            base_head=changed.base_head,
            reason_codes=tuple(sorted({code for code, _ in details})),
            reason_details=tuple(sorted(details)),
            reason=(
                f"Escalate: {file!r} matches shared-infrastructure pattern {pattern!r}; "
                "state the affected test lanes explicitly"
            ),
        )

    test_paths = paths_for(mapping_files)

    if not test_paths:
        return ScopedTestPlan(
            scope="none",
            changed_files=changed.files,
            sources=changed.sources,
            source_head=changed.source_head,
            base_head=changed.base_head,
            reader_registry_digest=reader_registry["digest"] if reader_registry else None,
            reason="Known non-testable paths only; no pytest command was selected",
        )

    if test_paths == full_suite:
        # Preserve every independent boundary even when one already widens.
        for path in mapping_files:
            if paths_for([path]) == full_suite:
                details.add(("UNKNOWN_SOURCE", path))
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            source_head=changed.source_head,
            base_head=changed.base_head,
            reason_codes=tuple(sorted({code for code, _ in details})),
            reason_details=tuple(sorted(details)),
            reason="Escalate: cross-cutting, migration, configuration, or unknown path detected",
        )

    existing_paths, notes = _normalise_existing_test_paths(
        test_paths, repo_dir=repo_dir, deleted_files=changed.deleted_files
    )
    if not existing_paths:
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            reason="Escalate: " + "; ".join(notes),
            reason_codes=("STATIC_TARGET_MISSING",),
        )
    if any(path.rstrip("/") in {p.rstrip("/") for p in full_suite} for path in existing_paths):
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            sources=changed.sources,
            source_head=changed.source_head,
            base_head=changed.base_head,
            reason="Escalate: selected parent is a complete configured root",
            reason_codes=("ROOT_WIDENING",),
        )

    reason = (
        f"Scoped to {len(existing_paths)} test path(s) from {len(changed.files)} changed file(s)"
    )
    if notes:
        reason += "; " + "; ".join(notes)
    root = Path(repo_dir or ".").resolve()
    selected = selected_test_files(root, existing_paths)
    if not selected:
        return ScopedTestPlan(
            scope="full",
            test_paths=full_suite,
            changed_files=changed.files,
            reason="Escalate: selected scope has no test files",
            reason_codes=("EMPTY_SCOPE",),
        )
    return ScopedTestPlan(
        scope="scoped",
        test_paths=existing_paths,
        changed_files=changed.files,
        sources=changed.sources,
        source_head=changed.source_head,
        base_head=changed.base_head,
        selected_files=tuple(selected),
        prediction=scope_cost.predict(
            root,
            selected,
            new_files=changed.added_files,
            planning=True,
        ),
        historical_manifest_witness=manifest_witness,
        reader_registry_digest=reader_registry["digest"] if reader_registry else None,
        reason=reason,
    )


def _mapped_plan(changed, *, repo_dir, fallback_allowlist):
    try:
        plan = _plan_for_changed_files(
            changed,
            repo_dir=repo_dir,
            fallback_allowlist=fallback_allowlist,
        )
        return replace(
            plan,
            omitted_untracked_files=tuple(
                sorted(
                    path
                    for path in changed.untracked_files
                    if path in UNTRACKED_RESIDUE and path not in changed.files
                )
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, subprocess.TimeoutExpired):
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            changed_files=changed.files,
            source_head=changed.source_head,
            base_head=changed.base_head,
            sources=changed.sources,
            reason="Escalate: source mapping evidence unavailable (MAPPING_UNAVAILABLE)",
            reason_codes=("MAPPING_UNAVAILABLE",),
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
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            reason="Escalate: immutable source unavailable (BASE_UNAVAILABLE)",
            reason_codes=("BASE_UNAVAILABLE",),
        )
    return _mapped_plan(changed, repo_dir=repo_dir, fallback_allowlist=fallback_allowlist)


def plan_worktree_tests(
    base: str = "origin/main",
    *,
    repo_dir: str | Path | None = None,
    fallback_allowlist: tuple[str, ...] = FULL_SUITE_FALLBACK_ALLOWLIST,
) -> ScopedTestPlan:
    """Plan tests for the current dirty worktree without executing pytest."""

    try:
        changed = get_worktree_changed_files(base, repo_dir=repo_dir)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return ScopedTestPlan(
            scope="full",
            test_paths=_full_suite(repo_dir),
            reason="Escalate: unable to compute worktree diff (BASE_UNAVAILABLE)",
            reason_codes=("BASE_UNAVAILABLE",),
        )
    return _mapped_plan(
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

    if plan.scope == "full":
        raise ValueError("FULL requires the named CI-shaped lanes; legacy execution refused")
    if plan.mode != "scoped":
        raise ValueError("Affected cost unavailable; legacy execution refused")

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
    parser.add_argument("--json", action="store_true", help="Print the versioned admission plan")
    args = parser.parse_args(argv)

    plan = (
        plan_scoped_tests(args.branch, base=args.base, repo_dir=args.repo_dir)
        if args.branch
        else plan_worktree_tests(base=args.base, repo_dir=args.repo_dir)
    )
    print(json.dumps(plan.as_dict(), sort_keys=True) if args.json else plan.report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
