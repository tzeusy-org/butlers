"""Availability regressions and opt-in installed RFC0036 feasibility evidence."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from tests.adapters import runtime_diagnostic_executor_feasibility_harness as harness


@pytest.mark.parametrize(
    "fault",
    [
        "candidate_missing",
        "fence_missing",
        "bootstrap",
        "proof",
        "candidate_launch",
        "candidate_execution",
        "receipt",
        "cleanup",
        "no_receipt",
        "malformed",
        "timeout",
        "postlaunch_crash",
        "candidate_timeout",
    ],
)
def test_supervisor_failures_do_not_claim_feasibility(fault):
    # Availability and supervisor failures are unit behavior, never feasibility proof.
    if fault == "candidate_timeout":
        for case in ("retry", "request_cap", "turn_cap"):
            receipt = {
                "measurement": "unknown",
                "eligibility": "unknown",
                "failure_stage": "candidate_execution",
                "control": case,
                "provider_attempts": 1 if case == "retry" else 3,
                "attachment_attempts": 1,
                "injected_calls": 1,
                "input_exceeded": False,
                "output_exceeded": False,
                "native_effect": False,
                "denials": [] if case == "retry" else [case],
                "positive": {"image_delivery": True, "persisted": False, "termination": "timeout"},
            }
            for measurement in ("unknown", "not_run", None):
                receipt["measurement"] = measurement
                observed = json.dumps(receipt, sort_keys=True)
                result = harness._control_result(receipt)
                assert result["status"] == "inconclusive"
                assert result["disposition"] == "unknown"
                assert result["completion"] == "incomplete"
                assert json.dumps(receipt, sort_keys=True) == observed
            receipt.update(measurement="completed", eligibility="unsupported", failure_stage=None)
            receipt["positive"]["termination"] = "exit"
            result = harness._control_result(receipt)
            assert result["status"] == "demonstrated"
            assert result["disposition"] == "supported"
            assert result["completion"] == "complete"
        return
    if fault in {"candidate_missing", "fence_missing"}:
        with patch.object(
            harness.shutil,
            "which",
            side_effect=[None if fault == "candidate_missing" else __file__, None],
        ):
            receipt = harness.run_installed("codex")
        assert receipt["measurement"] == "not_run"
        assert receipt["launched"] is False
    else:
        launched = fault in {"candidate_execution", "receipt", "cleanup", "postlaunch_crash"}
        stage = "candidate_execution" if fault == "postlaunch_crash" else fault
        event = json.dumps({"stage": stage, "launched": launched}) + "\n"
        result = subprocess.CompletedProcess([], 1, b"", event.encode())
        if fault == "malformed":
            result.stdout = b"not-json"
        if fault == "no_receipt":
            result.stderr = b""
        with (
            patch.object(harness.shutil, "which", return_value=__file__),
            patch.object(
                harness.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired([], 40) if fault == "timeout" else None,
                return_value=result,
            ),
        ):
            receipt = harness.run_installed("codex")
        assert receipt["measurement"] == "unknown"
        assert receipt["eligibility"] == "unknown"
        assert receipt["launched"] is (None if fault in {"no_receipt", "timeout"} else launched)
        assert receipt["failure_stage"] == (
            "receipt"
            if fault in {"no_receipt", "malformed"}
            else "supervisor_timeout"
            if fault == "timeout"
            else stage
        )
    assert "sentinel" not in json.dumps(receipt)
    assert all(value == "unmeasured" for value in receipt["obligations"].values())


@pytest.mark.parametrize("candidate", ["codex", "opencode"])
def test_installed_candidate_is_fenced_and_reports_every_obligation(candidate):
    if os.environ.get("BUTLERS_DIAGNOSTIC_FEASIBILITY") != "1":
        pytest.skip("Not run: opt-in installed evidence requires candidate and Bubblewrap")
    receipts = harness.measure_candidate(candidate)
    path = harness.write_evidence(candidate, receipts)
    assert path.exists() and path.with_suffix(".sha256").exists()
    assert len(receipts) == len(harness.INSTALLED_CASES)
    for receipt in receipts:
        assert receipt["measurement"] == "completed", receipt
        assert receipt["launched"] is True
        assert receipt["isolation"] == "supported"
        assert receipt["namespace_proof"]["interfaces"] == ["lo"]
        assert receipt["namespace_proof"]["external_connect"] == "unreachable"
        assert receipt["namespace_proof"]["host_mount"] == "absent"
        assert (
            receipt["candidate_digest"] and receipt["harness_digest"] and receipt["config_digest"]
        )
        assert set(receipt["obligations"]) == set(harness.OBLIGATIONS)
        assert receipt["eligibility"] == "unsupported"
        assert receipt["cleanup"] == "complete"
        outcome = receipt["control_result"]
        assert outcome["status"] in {"demonstrated", "not_exercised", "violation_observed"}
        if outcome["status"] == "demonstrated":
            assert outcome["exercised"] and not outcome["interference"]
        assert receipt["obligations"]["hard_output_token_cap"] == "unsupported"
        assert receipt["obligations"]["pre_request_budget_reservation"] == "unsupported"
