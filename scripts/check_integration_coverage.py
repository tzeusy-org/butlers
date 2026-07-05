#!/usr/bin/env python3
"""
check_integration_coverage.py

Guard against the "Integration tests (testcontainers)" CI job silently
under-collecting `pytest.mark.integration` tests.

Background (bu-m8cmk): the job used to pass an explicit, enumerated path list
(`roster/ tests/integration/ tests/config/ tests/core/ tests/migrations/`).
Any `@pytest.mark.integration` test living outside those specific
directories -- e.g. under `tests/modules/`, `tests/api/`, or a top-level
`tests/*.py` file -- was collected by NEITHER the "Unit tests" step (which
explicitly excludes `-m integration`) NOR the integration step (whose path
list didn't reach it). It silently never ran in CI. At the time this was
caught, 32 files / 193 tests had fallen through that gap.

This script makes that class of drift impossible to reintroduce silently: it
parses the *actual* `run:` command of the "Integration tests (testcontainers)"
step out of `.github/workflows/ci.yml`, collects (via `pytest --collect-only`,
no test execution, no DB required) exactly what that command would collect,
and compares it against a from-scratch collection of every
`pytest.mark.integration` test in the repo (using pytest's configured
`testpaths`, i.e. the whole codebase). If the job's command would miss any
test that is genuinely marked `integration` anywhere in the repo, this script
fails and lists exactly what would be skipped.

Exit codes:
  0  The integration job's path/marker spec collects every integration test.
  1  One or more integration tests would be silently skipped by CI.
  2  Could not locate or parse the CI step (repo layout changed).
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
STEP_NAME = "Integration tests (testcontainers)"


def _load_step_run_command(workflow_path: Path = CI_WORKFLOW_PATH) -> str:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                run = step.get("run")
                if not isinstance(run, str):
                    raise ValueError(f"Step {STEP_NAME!r} has no scalar 'run:' command")
                return run
    raise ValueError(f"Could not find a step named {STEP_NAME!r} in {workflow_path}")


def _parse_pytest_paths_and_marker(run_command: str) -> tuple[list[str], str]:
    tokens = shlex.split(run_command)
    try:
        pytest_index = tokens.index("pytest")
    except ValueError as exc:
        raise ValueError(f"No 'pytest' invocation found in run command: {run_command!r}") from exc

    paths: list[str] = []
    for token in tokens[pytest_index + 1 :]:
        if token.startswith("-"):
            break
        paths.append(token)
    if not paths:
        raise ValueError(f"No path arguments found before pytest flags in: {run_command!r}")

    marker: str | None = None
    for i, token in enumerate(tokens):
        if token == "-m" and i + 1 < len(tokens):
            marker = tokens[i + 1]
            break
    if marker is None:
        raise ValueError(f"No '-m <marker>' expression found in run command: {run_command!r}")

    return paths, marker


def _collect_node_ids(*, paths: list[str], marker: str) -> set[str]:
    cmd = [
        "uv",
        "run",
        "pytest",
        *paths,
        "--collect-only",
        "-q",
        "-m",
        marker,
        "-p",
        "no:cacheprovider",
    ]
    result = subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1, 5):
        # 0 = tests collected, 1 = collection error, 5 = no tests collected.
        # Anything else (2, 3, 4, ...) means pytest itself failed to run.
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"pytest --collect-only failed unexpectedly: {' '.join(cmd)}")

    node_ids = {
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "-", " "))
    }
    return node_ids


def main() -> int:
    try:
        run_command = _load_step_run_command()
        job_paths, marker = _parse_pytest_paths_and_marker(run_command)
    except ValueError as exc:
        print(f"check_integration_coverage: {exc}", file=sys.stderr)
        return 2

    print(f"Integration job command paths: {job_paths}")
    print(f"Integration job marker expr:   {marker!r}")

    job_collected = _collect_node_ids(paths=job_paths, marker=marker)
    # Ground truth: every test in the repo matching the same marker
    # expression, using pytest's configured `testpaths` (no path restriction).
    full_collected = _collect_node_ids(paths=[], marker=marker)

    missing = full_collected - job_collected
    if missing:
        missing_files = sorted({node_id.split("::", 1)[0] for node_id in missing})
        print(
            f"\nFAIL: {len(missing)} test(s) across {len(missing_files)} file(s) match "
            f"-m {marker!r} but would NOT be collected by the "
            f"'{STEP_NAME}' CI step's path list {job_paths}.\n"
            "This means they silently never run in CI.\n\n"
            "Missing files:",
            file=sys.stderr,
        )
        for f in missing_files:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nFix: widen the step's pytest path list (or the paths passed "
            "here) so it collects the whole repo, e.g. `tests/ roster/`.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: '{STEP_NAME}' collects all {len(full_collected)} integration test(s) in the repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
