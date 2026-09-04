"""Keep local CI lanes and hosted file shards contractually aligned."""

from __future__ import annotations

import os
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
    # dependency-free guard script in one job with nothing upstream of it.
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

    # Preflight and the shards depend only on the path classification: they
    # still overlap with each other to protect the budget, and they run on
    # every merge_group and on backend-touching pull requests only.
    backend_condition = (
        "github.event_name == 'merge_group' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.backend == 'true')"
    )
    for job in [preflight, *unit_jobs, *integration_jobs]:
        assert job["needs"] == ["changes"]
        assert job["if"] == backend_condition
    frontend_condition = (
        "github.event_name == 'merge_group' || github.event_name == 'push' || "
        "(github.event_name == 'pull_request' && needs.changes.outputs.frontend == 'true')"
    )
    for name in ("frontend", "frontend-e2e"):
        assert jobs[name]["needs"] == ["changes"]
        assert jobs[name]["if"] == frontend_condition

    assert check_job["needs"] == [
        "changes",
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
    ]
    assert check_job["if"] == "${{ always() }}"
    assert "cancelled" not in check_job["if"]

    for step_name in (
        "Check lock file is up to date",
        "Install dependencies",
        "Lint",
        "Format check",
        "SQL safety check (FOR UPDATE + outer join)",
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
    for result_name in (
        "EVENT_NAME",
        "CHANGES_RESULT",
        "CHANGES_BACKEND",
        "CHECK_PREFLIGHT_RESULT",
        *[f"CHECK_UNIT_{index}_RESULT" for index in range(1, 6)],
        *[f"CHECK_INTEGRATION_{index}_RESULT" for index in range(1, 6)],
    ):
        assert result_name in gate["run"]
    # Fail closed: a skipped shard passes only for a docs-only PR that the
    # `changes` job classified successfully, or on push to main (the queue
    # already validated that tree). Anything else, and any ran/skipped mix, fails.
    assert (
        '[ "$EVENT_NAME" = "pull_request" ] && [ "$CHANGES_RESULT" = "success" ] '
        '&& [ "$CHANGES_BACKEND" = "false" ]'
    ) in gate["run"]
    assert '[ "$EVENT_NAME" = "push" ]' in gate["run"]
    assert "skipped, but this event requires the shards to run" in gate["run"]
    assert "inconsistent shard state" in gate["run"]
    assert 'echo "shards_ran=true" >> "$GITHUB_OUTPUT"' in gate["run"]
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
