"""The one required check, ``alls-green``, fans in every CI workload (bu-29wkwj.3).

Branch protection requires only ``alls-green``. That is safe only while it directly needs
the complete workload set, cannot be skipped itself, and fails on any non-success result.
These tests read ``ci.yml`` structurally and execute the fan-in step's own shell.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
FAN_IN = "alls-green"
REQUIRED_WORKLOADS = frozenset(
    {
        "go-bridge",
        "guards",
        "smoke",
        "check-totals",
        "check-unit",
        "check-integration",
        "frontend-vitest",
        "frontend-assurance",
        "frontend-e2e",
    }
)
NON_SUCCESS_RESULTS = ("failure", "cancelled", "skipped", "", "unknown")


def _workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _fan_in_step(workflow: dict) -> dict:
    (step,) = workflow["jobs"][FAN_IN]["steps"]
    return step


def fan_in_is_complete(workflow: dict) -> bool:
    """True when alls-green directly needs exactly the required, defined workloads."""
    jobs = workflow["jobs"]
    needs = jobs[FAN_IN].get("needs", [])
    needs = [needs] if isinstance(needs, str) else list(needs)
    expected = _fan_in_step(workflow)["env"]["EXPECTED_JOBS"]
    return (
        len(needs) == len(set(needs))
        and set(needs) == REQUIRED_WORKLOADS
        and all(name in jobs for name in needs)
        and set(jobs) - {FAN_IN} == REQUIRED_WORKLOADS
        and str(expected) == str(len(needs))
    )


def _run_fan_in(needs_json: str, *, run: str | None = None) -> subprocess.CompletedProcess[str]:
    step = _fan_in_step(_workflow())
    assert shutil.which("jq"), "the alls-green step needs jq, as on GitHub-hosted runners"
    return subprocess.run(
        ["bash", "-e", "-c", run if run is not None else step["run"]],
        env={**os.environ, "NEEDS_JSON": needs_json, "EXPECTED_JOBS": step["env"]["EXPECTED_JOBS"]},
        capture_output=True,
        text=True,
        timeout=10,
    )


def _all_success() -> dict[str, dict]:
    return {name: {"result": "success", "outputs": {}} for name in sorted(REQUIRED_WORKLOADS)}


def test_alls_green_needs_the_complete_required_workload_set() -> None:
    workflow = _workflow()
    gate = workflow["jobs"][FAN_IN]

    assert fan_in_is_complete(workflow)
    assert gate["if"] == "always()"
    assert _fan_in_step(workflow)["env"]["NEEDS_JSON"] == "${{ toJSON(needs) }}"
    assert int(_fan_in_step(workflow)["env"]["EXPECTED_JOBS"]) == len(REQUIRED_WORKLOADS)


@pytest.mark.parametrize("dropped", sorted(REQUIRED_WORKLOADS))
def test_dropping_any_workload_from_the_fan_in_is_detected(dropped: str) -> None:
    workflow = copy.deepcopy(_workflow())
    workflow["jobs"][FAN_IN]["needs"].remove(dropped)

    assert not fan_in_is_complete(workflow)
    # Lowering the count to match does not hide the hole either.
    _fan_in_step(workflow)["env"]["EXPECTED_JOBS"] = str(len(REQUIRED_WORKLOADS) - 1)
    assert not fan_in_is_complete(workflow)


def test_an_unrequired_new_job_is_detected() -> None:
    workflow = copy.deepcopy(_workflow())
    workflow["jobs"]["new-workload"] = {"runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}

    assert not fan_in_is_complete(workflow)


def test_no_workload_can_be_skipped_or_soft_failed_by_configuration() -> None:
    jobs = _workflow()["jobs"]

    for name, job in jobs.items():
        # Any job-level condition (`if: false`, a route output) can skip a workload.
        if name != FAN_IN:
            assert "if" not in job, f"{name} has a job-level condition"
        assert "continue-on-error" not in job, f"{name} can soft-fail"
        assert "needs.route" not in json.dumps(job), f"{name} still reads the retired route job"


def test_ci_workflow_carries_no_inline_python_programs() -> None:
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert not re.search(r"<<-?\s*['\"]?(PY|PYEOF)\b", text)
    assert not re.search(r"\bpython3?\s+-(\s|$)", text, re.MULTILINE)
    assert "PYEOF" not in text


def test_fan_in_passes_only_when_every_workload_succeeded() -> None:
    result = _run_fan_in(json.dumps(_all_success()))

    assert result.returncode == 0, result.stderr
    for name in REQUIRED_WORKLOADS:
        assert f"{name}=success" in result.stdout


@pytest.mark.parametrize("verdict", NON_SUCCESS_RESULTS)
def test_fan_in_fails_on_any_non_success_result(verdict: str) -> None:
    for name in sorted(REQUIRED_WORKLOADS):
        needs = _all_success()
        needs[name]["result"] = verdict

        assert _run_fan_in(json.dumps(needs)).returncode != 0, (name, verdict)


def test_fan_in_fails_on_a_missing_or_extra_job() -> None:
    for name in sorted(REQUIRED_WORKLOADS):
        needs = _all_success()
        del needs[name]
        assert _run_fan_in(json.dumps(needs)).returncode != 0, name
        needs = _all_success()
        del needs[name]["result"]
        assert _run_fan_in(json.dumps(needs)).returncode != 0, f"{name} without result"
    extra = {**_all_success(), "unexpected": {"result": "success", "outputs": {}}}
    assert _run_fan_in(json.dumps(extra)).returncode != 0


@pytest.mark.parametrize("raw", ["{}", "", "null", "[]", '"not-an-object"', "{", "not json"])
def test_fan_in_fails_on_empty_or_malformed_needs(raw: str) -> None:
    assert _run_fan_in(raw).returncode != 0


def test_fan_in_count_check_is_load_bearing() -> None:
    """Fault control: without the length check, a missing job would pass."""
    run = _fan_in_step(_workflow())["run"]
    mutated = run.replace("length == $n and ", "")
    assert mutated != run
    needs = _all_success()
    del needs["frontend-e2e"]

    assert _run_fan_in(json.dumps(needs)).returncode != 0
    assert _run_fan_in(json.dumps(needs), run=mutated).returncode == 0
