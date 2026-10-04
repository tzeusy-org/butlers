"""Offline RFC0036 evidence; never invoke a candidate outside the fenced domain."""

import json

import pytest

from tests.adapters import runtime_diagnostic_executor_feasibility_harness as harness


@pytest.mark.parametrize("case", harness.CASES)
def test_mock_controls_are_independent(case):
    receipt = harness.exercise_control(case)
    assert receipt["disposition"] == ("supported" if case == "positive" else "unsupported")
    assert receipt["reason"] == case
    assert receipt["provider_requests"] <= 2
    assert receipt["attachment_calls"] <= 1
    assert receipt["effects"] == (1 if case == "positive" else 0)
    assert "sentinel" not in json.dumps(receipt)


@pytest.mark.parametrize("candidate", ["codex", "opencode"])
def test_installed_candidate_is_fenced_and_reports_every_obligation(candidate):
    receipt = harness.run_installed(candidate)
    assert set(receipt["obligations"]) == set(harness.OBLIGATIONS)
    assert all(value in {"supported", "unsupported"} for value in receipt["obligations"].values())
    assert receipt["eligibility"] == "unsupported"
    assert receipt["obligations"]["hard_output_token_cap"] == "unsupported"
    assert receipt["obligations"]["pre_request_budget_reservation"] == "unsupported"
    assert receipt["cleanup"] == "complete"
    if receipt["launched"]:
        assert receipt["isolation"] == "supported"
        assert receipt["namespace_proof"]["interfaces"] == ["lo"]
        assert receipt["namespace_proof"]["external_connect"] == "unreachable"
        assert receipt["namespace_proof"]["host_mount"] == "absent"
        assert receipt["candidate_digest"]
    else:
        assert receipt["reason"] in {"candidate_unavailable", "isolation_unavailable"}
