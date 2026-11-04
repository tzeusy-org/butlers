"""Finite PR5479 launcher contract; separate from the nine-workload source CI."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(os.environ.get("LAUNCHER_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[2]))
WORKFLOW = Path(__file__).parent / "session-host-causal.yml"
if not WORKFLOW.exists():
    WORKFLOW = ROOT / ".github/workflows/session-host-causal.yml"
SOURCE = "13ca133ad8c86f6ba23bf782449fcb9ac145f2da"
TREE = "2ce06bffae08d4bb243a1b7af7ae42f4b19df65f"
SEAL = "332465a8333a4e6a07a8c729831b56fbf744a13fec41fa18c3fa0ba5c8ea5ac6"
GUARD = " ".join(
    """
github.event_name == 'pull_request' &&
github.repository == 'tzeusy-org/butlers' &&
github.event.pull_request.number == 5479 &&
github.event.pull_request.base.ref == 'main' &&
github.event.pull_request.head.repo.full_name == 'tzeusy-org/butlers' &&
github.event.pull_request.head.ref == 'agent/bu-m1u7zy.4-20261104' &&
github.event.pull_request.head.sha == '32dcf18dfa1d68e5321df376f26876b791fa8a97'
""".split()
)


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _contract(workflow):
    """Exact finite admission, not a reusable arbitrary-workflow checker."""
    event = workflow.get("on", workflow.get(True))
    assert event == {"pull_request": {"types": ["opened", "synchronize", "reopened"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"causal"}
    job = workflow["jobs"]["causal"]
    assert " ".join(job["if"].split()) == GUARD
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 20
    assert job["env"]["PROBE_SOURCE_SHA"] == SOURCE
    assert job["env"]["PROBE_SOURCE_TREE"] == TREE
    assert job["env"]["PROBE_SEAL_SHA256"] == SEAL
    steps = job["steps"]
    assert steps[0]["with"] == {"ref": SOURCE, "persist-credentials": False}
    assert all("continue-on-error" not in step and "if" not in step for step in steps)
    names = [step.get("name", "") for step in steps]
    before = names.index("Verify reviewed source before privileged setup")
    setup = names.index("Set up the CI Python environment")
    native = names.index("Install native proof prerequisites")
    probe = names.index("Execute only the reviewed causal experiment")
    stage = names.index("Stage only closed successful evidence")
    assert before < setup < native < probe < stage == len(steps) - 2
    assert (
        steps[probe]["run"] == "uv run --no-sync python -B -m scripts.session_host_causal_probe "
        "--execute --disposable-job"
    )
    assert steps[probe]["timeout-minutes"] == 6
    assert steps[-1]["uses"] == "actions/upload-artifact@v4"
    assert steps[-1]["timeout-minutes"] == 2
    assert steps[-1]["with"]["path"].splitlines() == [
        "${{ runner.temp }}/session-host-causal-closed-evidence/report.json",
        "${{ runner.temp }}/session-host-causal-closed-evidence/binding.json",
    ]
    return steps[before]["run"], steps[stage]["run"]


def test_reviewed_launcher_has_only_the_finite_admitted_route():
    _contract(_workflow())
    # Source S and its actual tree must be frozen before this test is admitted.
    assert len(SOURCE) == 40 and set(SOURCE) <= set("0123456789abcdef")
    assert len(TREE) == 40 and set(TREE) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    "mutation",
    [
        "event",
        "runner",
        "source",
        "tree",
        "guard",
        "head_pin_changed",
        "head_pin_missing",
        "quarantine_upload",
        "quarantine_cleanup",
        "soft_failure",
        "artifact_glob",
    ],
)
def test_real_launcher_contract_refuses_unsafe_mutations(mutation):
    workflow = copy.deepcopy(_workflow())
    job = workflow["jobs"]["causal"]
    if mutation == "event":
        workflow.pop("on", None)
        workflow.pop(True, None)
        workflow["on"] = {"pull_request_target": {}}
    elif mutation == "runner":
        job["runs-on"] = "self-hosted"
    elif mutation == "source":
        job["steps"][0]["with"]["ref"] = "main"
    elif mutation == "tree":
        job["env"]["PROBE_SOURCE_TREE"] = "0" * 40
    elif mutation == "guard":
        job["if"] += " || true"
    elif mutation == "head_pin_changed":
        job["if"] = job["if"].replace("32dcf18dfa1d68e5321df376f26876b791fa8a97", "0" * 40)
    elif mutation == "head_pin_missing":
        job["if"] = " ".join(job["if"].split()).rsplit(" && ", 1)[0]
    elif mutation == "quarantine_upload":
        job["steps"][-1]["if"] = "always()"
    elif mutation == "quarantine_cleanup":
        job["steps"].append({"if": "always()", "run": "sudo apparmor_parser -R profile"})
    elif mutation == "soft_failure":
        job["steps"][-3]["continue-on-error"] = True
    else:
        job["steps"][-1]["with"]["path"] = "${{ runner.temp }}/**"
    with pytest.raises(AssertionError):
        _contract(workflow)


# Actual preflight/stager execution is owned by test_launcher_embedded_runtime.py.
