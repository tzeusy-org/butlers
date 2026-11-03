"""Every workflow job has a finite watchdog recorded in the timeout registry.

``scripts/ci-job-timeouts.json`` records each installed ``timeout-minutes`` with its
provenance. Provenance is before-only/provisional, never after calibration: the
arithmetic below checks each installed bound still retains its stated reserves.
"""

from __future__ import annotations

import json
import math
import shlex
from datetime import datetime
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "scripts/ci-job-timeouts.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text())


def _declared_timeouts() -> dict[tuple[str, str], int]:
    declared = {}
    for path in sorted((REPO_ROOT / ".github/workflows").glob("*.yml")):
        source = yaml.safe_load(path.read_text())
        for name, job in source["jobs"].items():
            value = job.get("timeout-minutes")
            if value == "${{ matrix.timeout }}":
                assert set(job["strategy"]["matrix"]) == {"include"}
                caps = [row["timeout"] for row in job["strategy"]["matrix"]["include"]]
                assert caps and all(type(cap) is int and cap > 0 for cap in caps)
                value = max(caps)
            assert isinstance(value, int) and not isinstance(value, bool) and value > 0, (
                f"{path.name}:{name} lacks a finite positive timeout-minutes"
            )
            declared[(str(path.relative_to(REPO_ROOT)), name)] = value
    return declared


def _job(workflow: str, job: str) -> dict:
    return yaml.safe_load((REPO_ROOT / ".github/workflows" / workflow).read_text())["jobs"][job]


def test_every_workflow_job_has_a_finite_timeout_matching_its_registry_row() -> None:
    declared = _declared_timeouts()
    register = _registry()

    rows = [(r["workflow"], r["job"]) for r in register["jobs"]]
    assert len(rows) == len(set(rows)), "duplicate registry rows"
    assert set(declared) == set(rows), (
        sorted(set(declared) - set(rows)),
        sorted(set(rows) - set(declared)),
    )
    for row in register["jobs"]:
        assert declared[(row["workflow"], row["job"])] == row["installed_timeout_minutes"]
        assert row["state"] and row["sample_source"]
        if "reviewed_job_name" in row:
            assert row["reviewed_job_name"] == row["job"]


def test_registry_bounds_retain_their_recorded_reserves() -> None:
    for row in _registry()["jobs"]:
        if row["workflow"] == ".github/workflows/ci.yml" and row["job"] == "go-bridge":
            assert row["successful_sample_n"] == 0 and row["p95_s"] is None
            assert not row.get("healthy_job_observations")
            assert row["additional_provisional_cold_dependency_allowance_s"] == 270
            assert row["installed_timeout_minutes"] * 60 == 600 + 30 + 270
            assert "provisional" in row["state"]
        if row["p95_s"] is not None:
            reserve = (
                row["review_uv_nominal_envelope_s"] + row["review_browser_hard_retry_interval_s"]
            )
            reserve += row["additional_provisional_cold_dependency_allowance_s"]
            assert row["installed_timeout_minutes"] >= max(
                2, math.ceil((2 * row["p95_s"] + reserve) / 60)
            ), row["job"]
        else:
            assert "provisional" in row["state"] or row["job"] == "faketime-matrix"
        observations = row.get("healthy_job_observations", [])
        if observations:
            # A single whole-job observation is a compatibility floor, not p95.
            # Keep setup/evidence time and the full extra recovery allowance.
            durations = []
            for observation in observations:
                assert observation["conclusion"] == "success"
                elapsed = (
                    datetime.fromisoformat(observation["completed_at"])
                    - datetime.fromisoformat(observation["started_at"])
                ).total_seconds()
                assert elapsed >= observation["test_step_elapsed_s"] > 0
                durations.append(elapsed)
            envelope = max(durations)
            assert row["provisional_healthy_envelope_s"] == envelope
            reserve = (
                row["review_uv_nominal_envelope_s"]
                + row["review_browser_hard_retry_interval_s"]
                + row["additional_provisional_cold_dependency_allowance_s"]
            )
            assert row["installed_timeout_minutes"] * 60 >= 2 * envelope + reserve, (
                f"{row['job']} watchdog excludes its recorded healthy whole-job envelope "
                "and full setup/recovery headroom"
            )


def test_faketime_matrix_watchdogs_are_finite_and_budgeted() -> None:
    nightly = _job("nightly.yml", "faketime-matrix")
    assert nightly["timeout-minutes"] == 136
    nightly_shell = "\n".join(step.get("run", "") for step in nightly["steps"])
    assert "WATCHDOG_SECONDS=6360" in nightly_shell
    assert "--signal=SIGABRT --kill-after=30s" in nightly_shell
    assert "--timeout=300 --timeout-method=thread" in nightly_shell
    assert nightly["strategy"] == {"fail-fast": False, "matrix": {"offset": ["+45d", "+120d"]}}
    suite = next(
        step
        for step in nightly["steps"]
        if step.get("name") == "Full suite at faketime offset ${{ matrix.offset }}"
    )
    suite_argv = shlex.split(
        suite["run"][suite["run"].index("uv run pytest ") :].replace("\\\n", " ")
    )
    # Both shifted PostgreSQL legs admit clock cases; retain the independent
    # exclusions and complete tests/roster scope, distribution and finite timers.
    assert suite_argv == [
        "uv",
        "run",
        "pytest",
        "tests/",
        "roster/",
        "-v",
        "-rf",
        "--tb=short",
        "--ignore=tests/e2e",
        "-m",
        "not bench and not perf and not nightly and not faketime_fragile",
        "-n",
        "auto",
        "--dist",
        "loadfile",
        "--timeout=300",
        "--timeout-method=thread",
    ]
    budget = next(row for row in _registry()["jobs"] if row["job"] == "faketime-matrix")
    assert budget["successful_sample_n"] == 2 and budget["p95_s"] is None
    observations = budget["healthy_job_observations"]
    assert {observation["offset"] for observation in observations} == {"+45d", "+120d"}
    assert len(observations) == 2  # One historical observation per distinct offset, not p95.
    assert all(
        observation["run_id"] == 39802115884
        and observation["head_sha"] == "d5132bdeb7f12a49d969ac04a6459b29d7584d5b"
        for observation in observations
    )
    assert sorted(observation["test_step_elapsed_s"] for observation in observations) == [
        2198,
        3173,
    ]
    assert budget["provisional_healthy_envelope_s"] == 3271
    suite_envelope_s = max(observation["test_step_elapsed_s"] for observation in observations)
    assert budget["installed_suite_watchdog_s"] == math.ceil(2 * suite_envelope_s / 60) * 60
    assert budget["installed_suite_watchdog_s"] == 6360
    assert budget["review_uv_nominal_envelope_s"] == 640
    assert budget["review_browser_hard_retry_interval_s"] == 0
    reserves = budget["provisional_extra_reserves_s"]
    assert reserves == {"frozen_dependencies": 640, "setup_and_evidence": 300, "abort_cleanup": 30}
    assert budget["additional_provisional_cold_dependency_allowance_s"] == sum(reserves.values())
    assert nightly["timeout-minutes"] == math.ceil((2 * 3271 + 640 + sum(reserves.values())) / 60)
    # Even a suite reaching its own cap retains the full extra reserves and
    # the largest observed setup/finalization time; no first-attempt subtraction.
    observed_other_job_s = max(
        (
            datetime.fromisoformat(observation["completed_at"])
            - datetime.fromisoformat(observation["started_at"])
        ).total_seconds()
        - observation["test_step_elapsed_s"]
        for observation in observations
    )
    assert nightly["timeout-minutes"] * 60 >= (
        budget["installed_suite_watchdog_s"]
        + budget["review_uv_nominal_envelope_s"]
        + sum(reserves.values())
        + observed_other_job_s
    )
