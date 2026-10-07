"""Keep local CI lanes and hosted file shards contractually aligned."""

from __future__ import annotations

import copy
import fractions
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("make") is None, reason="requires make"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("target", "fragments"),
    [
        (
            "test-ci-unit",
            (
                "scripts/pytest_gate.py run",
                "git status --porcelain",
                "git rev-parse HEAD",
                "clean=true",
                "tests/ roster/",
                "-q",
                "--maxfail=1",
                "--tb=short",
                "--ignore=tests/e2e",
                "not integration and not e2e and not nightly and not bench and not perf",
                "--cov=src/butlers",
                "--cov-report=json:coverage.json",
                "--cov-report=term-missing",
            ),
        ),
        (
            "test-ci-integration",
            (
                "scripts/pytest_gate.py run",
                "git status --porcelain",
                "git rev-parse HEAD",
                "clean=true",
                "tests/ roster/",
                "-q",
                "--maxfail=5",
                "--tb=short",
                "integration and not nightly and not bench and not perf",
                "-n auto --dist loadfile",
                "--cov=src/butlers",
                "--cov-append",
                "--cov-report=json:coverage.json",
                "--cov-report=term-missing",
            ),
        ),
    ],
)
def test_local_ci_targets_are_receipt_producing_and_keep_full_lane_scope(
    target: str, fragments: tuple[str, ...]
) -> None:
    dry_run = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    for fragment in fragments:
        assert fragment in dry_run, f"`make {target}` lost CI fragment {fragment!r}:\n{dry_run}"


def _workflow_step(*, job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing {name!r} step")


def _artifact_step(*, job: dict, artifact_name: str) -> dict:
    return next(
        step
        for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
        and step["with"]["name"] == artifact_name
    )


def _run_fan_in(
    *, gate: dict, needs: object, event: str, ref: str, tmp_path: Path, raw_needs: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Execute the actual YAML step, including the old per-result transport."""
    output = tmp_path / "gate-output"
    output.write_text("")
    env = {**os.environ, "EVENT_NAME": event, "REF": ref, "GITHUB_OUTPUT": str(output)}
    env["NEEDS_JSON"] = json.dumps(needs) if raw_needs is None else raw_needs
    # The baseline uses separate env fields. Resolve those actual expressions
    # too, so the old-code control reaches ignored verdicts rather than setup.
    for name, expression in gate["env"].items():
        match = re.fullmatch(r"\$\{\{ needs\.([\w-]+)\.(result|outputs\.\w+) \}\}", expression)
        if match and isinstance(needs, dict):
            job, field = match.groups()
            value = needs.get(job, {})
            for part in field.split("."):
                value = value.get(part, "") if isinstance(value, dict) else ""
            env[name] = value if isinstance(value, str) else ""
    result = subprocess.run(
        ["bash", "-e", "-c", gate["run"]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result, output.read_text()


def test_ci_gate_reads_every_needed_verdict_without_counting_preflight_as_a_shard(
    tmp_path: Path,
) -> None:
    """REQ-testing-035: actual verdict consumer, not a reimplementation of it."""
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    gate = next(step for step in jobs["check"]["steps"] if step.get("id") == "gate")
    heavy = {name for name in jobs if name.startswith(("check-unit-", "check-integration-"))}
    full = {
        name: {"result": "success", "outputs": {}} for name in {*jobs["check"]["needs"], "guards"}
    }
    full["changes"]["outputs"] = {"backend": "true", "frontend": "false"}
    full["plan"]["outputs"] = {"mode": "full", "test_paths": "[]"}
    full["check-affected"]["result"] = "skipped"
    contexts = [("full", "pull_request", "refs/pull/1/merge", full, True)]
    scoped = copy.deepcopy(full)
    for name in heavy:
        scoped[name]["result"] = "skipped"
    scoped["plan"]["outputs"] = {
        "mode": "scoped",
        "test_paths": '["tests/contracts/test_ci_test_targets.py"]',
    }
    scoped["check-affected"]["result"] = "success"
    contexts.append(("scoped", "pull_request", "refs/pull/1/merge", scoped, False))
    docs = copy.deepcopy(scoped)
    docs["changes"]["outputs"] = {"backend": "false", "frontend": "false"}
    for name in ("plan", "check-preflight", "check-affected"):
        docs[name] = {"result": "skipped", "outputs": {}}
    contexts.append(("docs", "pull_request", "refs/pull/1/merge", docs, False))
    push = copy.deepcopy(docs)
    push["changes"]["outputs"] = {"backend": "true", "frontend": "true"}
    contexts.append(("push", "push", "refs/heads/main", push, False))
    merge_group = copy.deepcopy(full)
    merge_group["plan"] = {"result": "skipped", "outputs": {}}
    merge_group["changes"]["outputs"]["frontend"] = "true"
    contexts.append(
        ("merge_group", "merge_group", "refs/heads/gh-readonly-queue/main/x", merge_group, True)
    )

    failures = []

    def check(label: str, needs: object, event: str, ref: str, expected: bool) -> None:
        result, output = _run_fan_in(
            gate=gate, needs=needs, event=event, ref=ref, tmp_path=tmp_path
        )
        if (result.returncode == 0) != expected:
            failures.append(f"{label}: exit={result.returncode}, expected_success={expected}")
        if expected:
            ran = event == "merge_group" or (
                event == "pull_request" and needs["plan"]["outputs"].get("mode") == "full"
            )
            assert f"shards_ran={str(ran).lower()}\n" == output, label

    for mode, event, ref, needs, _ in contexts:
        check(mode, needs, event, ref, True)
        for name in ("check-preflight", *sorted(set(needs) - {"check-preflight"})):
            for verdict in ("failure", "cancelled", "", "unknown", False, None):
                changed = copy.deepcopy(needs)
                changed[name]["result"] = verdict
                check(f"{mode}/{name}/{verdict!r}", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            del changed[name]
            check(f"{mode}/{name}/missing", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            del changed[name]["result"]
            check(f"{mode}/{name}/missing-result", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            changed[name]["outputs"] = []
            check(f"{mode}/{name}/invalid-outputs", changed, event, ref, False)
        for result in ("success", "skipped", "failure", "cancelled", "unknown"):
            changed = copy.deepcopy(needs)
            changed["new-needed-job"] = {"result": result, "outputs": {}}
            check(f"{mode}/added/{result}", changed, event, ref, result == "success")
    for mode, event, ref, needs, _ in contexts:
        for name in ("changes", "guards", "plan", "check-preflight", "check-affected", *heavy):
            changed = copy.deepcopy(needs)
            changed[name]["result"] = "skipped" if needs[name]["result"] == "success" else "success"
            check(f"{mode}/{name}/wrong-pairing", changed, event, ref, False)
    for output in (
        {},
        {"backend": "true"},
        {"backend": True, "frontend": "false"},
        {"backend": "invalid", "frontend": "false"},
        {"backend": "false", "frontend": False},
    ):
        changed = copy.deepcopy(full)
        changed["changes"]["outputs"] = output
        check(
            f"invalid-classification/{output}", changed, "pull_request", "refs/pull/1/merge", False
        )
    for output in (
        {},
        {"mode": "invalid", "test_paths": "[]"},
        {"mode": "scoped", "test_paths": "[]"},
        {"mode": "full", "test_paths": '["tests/test_x.py"]'},
        {"mode": "scoped", "test_paths": "invalid"},
        {"mode": "scoped", "test_paths": '["../outside.py"]'},
    ):
        changed = copy.deepcopy(full)
        changed["plan"]["outputs"] = output
        check(f"invalid-plan/{output}", changed, "pull_request", "refs/pull/1/merge", False)
    check("non-main-push", push, "push", "refs/heads/other", False)
    check("unknown-event", full, "workflow_dispatch", "refs/heads/main", False)
    for malformed in ([], None, {}, "not-an-object"):
        check(f"invalid-needs/{malformed!r}", malformed, "merge_group", "refs/heads/main", False)
    for raw in ("{", json.dumps(full)[:-1] + ', "changes": {}}'):
        result, output = _run_fan_in(
            gate=gate,
            needs=full,
            raw_needs=raw,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        assert result.returncode != 0
        assert "shards_ran=true" not in output
    assert not failures, "\n".join(failures)

    # Reached fault controls prove that each independent policy check matters.
    # They edit only the extracted actual step, never another implementation.
    mutations = [
        (
            "preflight",
            gate["run"].replace(
                "invalid = []", 'needs["check-preflight"]["result"] = "success"\ninvalid = []'
            ),
            "check-preflight",
            "failure",
        ),
        (
            "guards",
            gate["run"].replace(
                "invalid = []", 'needs["guards"]["result"] = "success"\ninvalid = []'
            ),
            "guards",
            "failure",
        ),
        (
            "default-skip",
            gate["run"].replace(
                'expected.get(name, "success")', 'expected.get(name, job["result"])'
            ),
            "new-needed-job",
            "skipped",
        ),
        (
            "partial-heavy",
            gate["run"].replace(
                'if job["result"] != expected.get(name, "success")',
                'if name not in heavy and job["result"] != expected.get(name, "success")',
            ),
            sorted(heavy)[0],
            "skipped",
        ),
    ]
    for label, mutated, job_name, verdict in mutations:
        assert mutated != gate["run"], label
        changed = copy.deepcopy(full)
        changed[job_name] = {"result": verdict, "outputs": {}}
        ordinary, _ = _run_fan_in(
            gate=gate,
            needs=changed,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        neutralized, _ = _run_fan_in(
            gate={**gate, "run": mutated},
            needs=changed,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        assert ordinary.returncode != 0, label
        assert neutralized.returncode == 0, (label, neutralized.stderr)


def test_smoke_ci_spec_matches_the_preflight_topology() -> None:
    spec = (REPO_ROOT / "openspec/specs/testing/spec.md").read_text(encoding="utf-8")

    assert "### Requirement: Smoke Tests Run In CI As A Fast Gate" in spec
    assert "- **WHEN** the CI `check-preflight` job runs" in spec
    assert "and run alongside the independent unit and integration shards" in spec


def _integration_cleanup_script(shard: int) -> str:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return _workflow_step(
        job=workflow["jobs"][f"check-integration-{shard}"],
        name="Free disk space before testcontainers",
    )["run"]


def _run_cleanup_script(
    *, tmp_path: Path, shard: int, available_kib: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    sentinel = tmp_path / "sudo-called"
    (fake_bin / "df").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-h" ]; then\n'
        "  printf '%s\\n' 'Filesystem Size Used Avail Use% Mounted on'\n"
        "  printf '%s\\n' '/dev/root 145G 65G 80G 45% /'\n"
        "else\n"
        "  printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        "  printf '/dev/root 152043520 68157440 %s 45%% /\\n' \"$DF_AVAILABLE_KIB\"\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "sudo").write_text(
        '#!/usr/bin/env bash\nprintf "called\\n" > "$SUDO_SENTINEL"\nexit 99\n',
        encoding="utf-8",
    )
    for command in (fake_bin / "df", fake_bin / "sudo"):
        command.chmod(0o755)

    result = subprocess.run(
        ["bash", "-e", "-c", _integration_cleanup_script(shard)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DF_AVAILABLE_KIB": available_kib,
            "SUDO_SENTINEL": str(sentinel),
        },
    )
    return result, sentinel


def test_ci_cleanup_skips_reclamation_when_each_runner_has_safe_free_space(
    tmp_path: Path,
) -> None:
    for shard in range(1, 6):
        result, sentinel = _run_cleanup_script(
            tmp_path=tmp_path / f"shard-{shard}",
            shard=shard,
            available_kib=str(80 * 1024 * 1024),
        )
        assert result.returncode == 0, result.stderr
        assert "Skipping disk cleanup:" in result.stdout
        assert not sentinel.exists()


def test_ci_cleanup_reclaims_when_each_runner_is_below_the_safe_floor(tmp_path: Path) -> None:
    for shard in range(1, 6):
        result, sentinel = _run_cleanup_script(
            tmp_path=tmp_path / f"shard-{shard}",
            shard=shard,
            available_kib=str(29 * 1024 * 1024),
        )
        assert result.returncode == 99
        assert "Cleaning disk:" in result.stdout
        assert sentinel.exists()


@pytest.mark.parametrize("available_kib", ["", "-1", "30.5", "N/A", "abc"])
def test_ci_cleanup_refuses_malformed_free_space_before_reclamation(
    tmp_path: Path, available_kib: str
) -> None:
    result, sentinel = _run_cleanup_script(
        tmp_path=tmp_path,
        shard=1,
        available_kib=available_kib,
    )
    assert result.returncode == 1
    assert "could not determine free disk space" in result.stdout
    assert not sentinel.exists()


def test_ci_workflow_shards_full_lanes_without_coverage_or_privacy_drift() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    preflight = jobs["check-preflight"]
    unit_jobs = [jobs[f"check-unit-{index}"] for index in range(1, 6)]
    integration_jobs = [jobs[f"check-integration-{index}"] for index in range(1, 6)]
    check_job = jobs["check"]

    # Merge-queue topology (bu-r5mnn): the queue's merge_group run is the terminal
    # broad gate, so the workflow must accept that event.
    assert set(workflow[True]) == {"push", "pull_request", "merge_group"}

    # `changes` classifies the PR diff fail-closed; `guards` runs every
    # static guard script in one job with nothing upstream of it.
    changes = jobs["changes"]
    assert "needs" not in changes and "if" not in changes
    assert set(changes["outputs"]) == {"backend", "frontend"}
    path_filter = _workflow_step(job=changes, name="Filter changed paths")
    assert path_filter["uses"].startswith("dorny/paths-filter@")
    assert path_filter["if"] == "github.event_name == 'pull_request'"
    assert path_filter["with"]["list-files"] == "json"
    classify = _workflow_step(job=changes, name="Classify the diff (fail closed)")
    assert classify["id"] == "classify"
    guards = jobs["guards"]
    assert "needs" not in guards and "if" not in guards
    guard_outcomes = _workflow_step(job=guards, name="Fail if any guard failed")["env"][
        "GUARD_OUTCOMES"
    ]
    for guard_id in (
        "lock",
        "lint",
        "format",
        "sql_safety",
        "session_links",
        "em_dashes",
        "spec_overwrites",
        "openspec_strict",
        "archived_requirements",
        "countable_tasks",
        "cited_requirements",
        "frontend_copy_regenerate",
        "frontend_copy",
        "duplicate_names",
    ):
        guard_step = next(step for step in guards["steps"] if step.get("id") == guard_id)
        assert "!cancelled()" in guard_step["if"]  # One failing guard never hides another.
        assert f"{guard_id}=${{{{ steps.{guard_id}.outcome }}}}" in guard_outcomes

    # Execute the actual finalizer: skipped mandatory checks cannot turn green,
    # while the intentional non-PR session-link skip has a positive companion.
    finalizer = _workflow_step(job=guards, name="Fail if any guard failed")
    outcomes = {line.split("=", 1)[0]: "success" for line in guard_outcomes.splitlines()}
    for name, verdict, event, expected in [
        ("session_links", "skipped", "merge_group", 0),
        ("session_links", "skipped", "pull_request", 1),
        *[
            (name, result, "pull_request", 1)
            for name in outcomes
            for result in ("failure", "skipped", "cancelled")
        ],
    ]:
        actual = {**outcomes, name: verdict}
        result = subprocess.run(
            ["bash", "-e", "-c", finalizer["run"]],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "GUARD_OUTCOMES": "\n".join(f"{key}={value}" for key, value in actual.items()),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == expected, (name, verdict, result.stdout)

    static_commands = {
        "Check lock file is up to date": "uv lock --check",
        "Lint": "make lint",
        "Format check": "uv run ruff format --check src/ tests/ roster/ conftest.py -q",
        "SQL safety check (FOR UPDATE + outer join)": "make check-for-update-joins",
    }
    for name, command in static_commands.items():
        assert _workflow_step(job=guards, name=name)["run"] == command
        assert not any(step.get("name") == name for step in preflight["steps"])
    assert (
        _workflow_step(job=guards, name="Install uv")["run"]
        == _workflow_step(job=preflight, name="Install uv")["run"]
    )
    assert (
        _workflow_step(job=guards, name="Install dependencies")["run"] == "uv sync --frozen --dev"
    )
    ordered = [step.get("name") for step in guards["steps"]]
    assert (
        ordered.index("Install uv")
        < ordered.index("Check lock file is up to date")
        < ordered.index("Install dependencies")
        < ordered.index("Lint")
    )
    dry_run = subprocess.run(
        ["make", "-n", "check-guards"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    for command in (
        "uv lock --check",
        "uv run ruff check src/ tests/",
        static_commands["Format check"],
        "scripts/check_for_update_joins.py",
    ):
        assert command in dry_run

    # Preflight and the shards depend only on the path classification: they
    # still overlap with each other to protect the budget, and they run on
    # every merge_group and on backend-touching pull requests only.
    backend_condition = (
        "github.event_name == 'merge_group' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.backend == 'true')"
    )
    assert preflight["needs"] == ["changes"]
    assert preflight["if"] == backend_condition

    # The ten shards additionally depend on the affected-test planner
    # (bu-v28ho): on a pull_request they only run when the plan did not
    # select a scoped mode -- a scoped plan means `check-affected` is that
    # shard's evidence instead. merge_group is unaffected: `plan` never runs
    # there, so `needs.plan.outputs.mode` is empty and the OR's first branch
    # (always true for merge_group) still wins -- PROVIDED the job's `if:`
    # actually gets evaluated. `plan` is a conditionally-skipped dependency,
    # and GitHub Actions auto-skips a job whose `needs` include a
    # skipped/failed job UNLESS its own `if:` starts with `always()`; without
    # that, this shard's `if:` above would never be reached on merge_group
    # (where `plan` is always skipped) and the job would silently skip
    # instead of running -- exactly the incident this assertion guards
    # against (bu-v28ho merge_group eviction, 2026-09-04).
    shard_condition = (
        "always() && needs.changes.result == 'success' && "
        "(github.event_name == 'merge_group' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.backend == 'true' "
        "&& needs.plan.outputs.mode != 'scoped'))"
    )
    for job in [*unit_jobs, *integration_jobs]:
        assert job["needs"] == ["changes", "plan"]
        assert job["if"] == shard_condition

    plan_job = jobs["plan"]
    assert plan_job["needs"] == ["changes"]
    assert plan_job["if"] == (
        "github.event_name == 'pull_request' && needs.changes.outputs.backend == 'true'"
    )
    assert set(plan_job["outputs"]) == {"mode", "test_paths"}

    check_affected = jobs["check-affected"]
    assert check_affected["needs"] == ["changes", "plan"]
    assert check_affected["if"] == (
        "always() && needs.changes.result == 'success' && "
        "github.event_name == 'pull_request' && needs.plan.outputs.mode == 'scoped'"
    )

    # Regression guard (bu-v28ho): every job that lists `plan` as a
    # dependency must opt out of GitHub Actions' implicit
    # skip-if-a-needed-job-was-skipped-or-failed behavior with `always()` at
    # the front of its `if:`. A job that adds `plan` to `needs` later without
    # this prefix will silently never run whenever `plan` is skipped (every
    # merge_group event, every docs-only or non-backend pull_request).
    for job_name, job in jobs.items():
        if "plan" in job.get("needs", []):
            condition = job["if"].removeprefix("${{").strip()
            assert condition.startswith("always()"), (
                f"{job_name}: needs `plan` but its `if:` does not start with always(), "
                "so it will silently skip whenever `plan` is skipped (e.g. every "
                "merge_group event)"
            )

    frontend_condition = (
        "github.event_name == 'merge_group' || github.event_name == 'push' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.frontend == 'true')"
    )
    for name in ("frontend", "frontend-e2e"):
        assert jobs[name]["needs"] == ["changes"]
        assert jobs[name]["if"] == frontend_condition

    assert check_job["needs"] == [
        "changes",
        "guards",
        "plan",
        "check-preflight",
        "check-unit-1",
        "check-unit-2",
        "check-unit-3",
        "check-unit-4",
        "check-unit-5",
        "check-integration-1",
        "check-integration-2",
        "check-integration-3",
        "check-integration-4",
        "check-integration-5",
        "check-affected",
    ]
    assert check_job["if"] == "${{ always() }}"
    assert "cancelled" not in check_job["if"]

    for step_name in (
        "Install dependencies",
        "Verify CI test shard manifests",
        "Smoke tests (fast gate + release evidence)",
    ):
        _workflow_step(job=preflight, name=step_name)
    assert (
        "check_ci_test_shards.py"
        in _workflow_step(job=preflight, name="Verify CI test shard manifests")["run"]
    )
    assert "check_integration_coverage.py" not in str(preflight)

    for job in [preflight, *unit_jobs, *integration_jobs]:
        assert job["services"]["postgres"]["image"] == "postgres:16"
        assert job["env"]["DATABASE_URL"] == "postgresql://postgres:test@localhost:5432/postgres"

    expected_coverage_artifacts: list[tuple[str, str, str, str]] = []
    for index, job in enumerate(unit_jobs, start=1):
        run_step = _workflow_step(job=job, name=f"Unit tests (shard {index})")
        assert run_step["run"] == (
            f"uv run python scripts/check_ci_test_shards.py run --lane unit --shard {index}"
        )
        assert run_step["env"]["COVERAGE_FILE"].endswith(f"coverage-unit-{index}.data")
        assert run_step["env"]["TEST_EVIDENCE_DIR"].endswith(f"ci-artifacts/unit-{index}")
        expected_coverage_artifacts.append(
            (
                f"ci-unit-{index}-coverage-data",
                f"coverage-unit-{index}.data",
                f"Download unit shard {index} coverage data",
                f"ci-coverage/unit-{index}",
            )
        )

    for index, job in enumerate(integration_jobs, start=1):
        cleanup = _workflow_step(job=job, name="Free disk space before testcontainers")
        for fragment in (
            "MIN_FREE_GB=30",
            "available_kib=$(df -Pk / | awk 'NR == 2 {print $4}')",
            'case "$available_kib" in',
            "available_gb=$((available_kib / 1024 / 1024))",
            'if [ "$available_gb" -lt "$MIN_FREE_GB" ]; then',
            "sudo rm -rf /usr/local/lib/android",
            "Skipping disk cleanup:",
            "could not determine free disk space",
        ):
            assert fragment in cleanup["run"]
        run_step = _workflow_step(job=job, name=f"Integration tests (shard {index})")
        assert run_step["run"] == (
            f"uv run python scripts/check_ci_test_shards.py run --lane integration --shard {index}"
        )
        assert run_step["env"]["COVERAGE_FILE"].endswith(f"coverage-integration-{index}.data")
        assert run_step["env"]["TEST_EVIDENCE_DIR"].endswith(f"ci-artifacts/integration-{index}")
        assert run_step["env"]["TESTCONTAINERS_RYUK_DISABLED"] == "true"
        expected_coverage_artifacts.append(
            (
                f"ci-integration-{index}-coverage-data",
                f"coverage-integration-{index}.data",
                f"Download integration shard {index} coverage data",
                f"ci-coverage/integration-{index}",
            )
        )

    artifact_steps = {
        step["with"]["name"]: step
        for job in [preflight, *unit_jobs, *integration_jobs, check_job]
        for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    }
    expected_artifacts = {
        "smoke-release-evidence",
        "ci-smoke-test-evidence",
        "ci-combined-coverage-report",
        *{name for name, _, _, _ in expected_coverage_artifacts},
        *{
            f"ci-{lane}-{index}-test-evidence"
            for lane, count in (("unit", 5), ("integration", 5))
            for index in range(1, count + 1)
        },
    }
    assert expected_artifacts <= artifact_steps.keys()
    for artifact_name in expected_artifacts:
        artifact = artifact_steps[artifact_name]
        assert artifact["with"]["overwrite"] is True
        assert ".tmp" not in artifact["with"]["path"]
        assert "raw-junit.xml" not in artifact["with"]["path"]

    for coverage_name, filename, download_name, directory in expected_coverage_artifacts:
        coverage_upload = artifact_steps[coverage_name]
        assert coverage_upload["if"] == "always()"
        assert coverage_upload["with"]["if-no-files-found"] == "error"
        assert coverage_upload["with"]["path"].endswith(filename)
        download = _workflow_step(job=check_job, name=download_name)
        assert download["uses"] == "actions/download-artifact@v4"
        assert download["with"] == {
            "name": coverage_name,
            "path": "${{ runner.temp }}/" + directory,
        }

    gate = _workflow_step(
        job=check_job,
        name="Require preflight and every test shard to pass (or be skipped by the path filter)",
    )
    assert gate["id"] == "gate"
    assert gate["env"] == {
        "EVENT_NAME": "${{ github.event_name }}",
        "REF": "${{ github.ref }}",
        "NEEDS_JSON": "${{ toJSON(needs) }}",
    }
    for step in check_job["steps"][1:]:
        assert "steps.gate.outputs.shards_ran == 'true'" in step["if"]

    combine = _workflow_step(
        job=check_job, name="Combine coverage from all independent test shards"
    )
    assert "coverage combine --data-file=" in combine["run"]
    for prefix, count in (("UNIT", 5), ("INTEGRATION", 5)):
        for index in range(1, count + 1):
            assert f"{prefix}_{index}_COVERAGE" in combine["run"]
    assert 'test -s "$coverage_file"' in combine["run"]

    smoke = _workflow_step(job=preflight, name="Smoke tests (fast gate + release evidence)")
    assert smoke["env"]["TESTCONTAINERS_RYUK_DISABLED"] == "true"
    assert smoke["env"]["SMOKE_EVIDENCE_DIR"].endswith("ci-artifacts/smoke")
    assert "--durations" not in smoke["run"]
    smoke_artifact = artifact_steps["smoke-release-evidence"]
    assert smoke_artifact["with"]["path"].endswith("smoke/release-evidence.json")

    badge = _workflow_step(job=check_job, name="Update coverage badge")
    assert badge["if"] == (
        "${{ steps.gate.outputs.shards_ran == 'true' && github.event_name == 'merge_group' }}"
    )
