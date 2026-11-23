"""bu-ejow5p.11: verifier planning; inverse controls remove exactly one hardening."""

from __future__ import annotations

import copy
import uuid

import pytest

from butlers.core.qa import isolation_verifier as iv
from butlers.core.qa.investigator_job import build_investigator_job

pytestmark = pytest.mark.unit

_IMAGE = "registry.invalid/butlers-app@sha256:" + "a" * 64


def _job():
    return iv.build_probe_job(
        uuid.UUID(int=1),
        release="butlers",
        namespace="butlers-dev",
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        command=iv.probe_command(refused_tcp=["pg=db:5432"], readable_path=["/workspace"]),
        deadline_s=300,
    )


def test_the_probe_job_is_the_production_template_with_only_the_command_changed() -> None:
    probe = _job()
    prod = build_investigator_job(
        attempt_id=uuid.UUID(int=1),
        release="butlers",
        namespace="butlers-dev",
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        command=["x"],
        deadline_s=300,
    )
    probe_c = copy.deepcopy(probe)
    prod_c = copy.deepcopy(prod)
    probe_c["spec"]["template"]["spec"]["containers"][0]["command"] = ["x"]
    assert probe_c == prod_c
    assert probe["spec"]["template"]["spec"]["containers"][0]["command"][:4] == [
        "python",
        "-I",
        "-m",
        iv.PROBE_MODULE,
    ]


@pytest.mark.parametrize("control", iv.INVERSE_CONTROLS, ids=lambda c: c.name)
def test_each_inverse_control_changes_something_and_leaves_the_original_alone(control) -> None:
    job = _job()
    before = copy.deepcopy(job)
    mutated = iv.render_inverse(job, control)
    assert job == before
    assert mutated != job


def test_host_pid_is_the_only_control_expected_to_be_refused_by_admission() -> None:
    assert [c.name for c in iv.INVERSE_CONTROLS if c.expect == iv.VAP_REFUSED] == ["host_pid"]


def _receipt(**outcomes):
    return {
        "passed": all(v == "pass" for v in outcomes.values()),
        "checks": [{"check": k, "outcome": v} for k, v in outcomes.items()],
    }


def test_a_control_needs_red_when_removed_and_pass_when_restored() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "run_as_root")
    good = iv.judge_control(control, _receipt(uid_not_root="fail"), _receipt(uid_not_root="pass"))
    assert good.ok
    never_red = iv.judge_control(
        control, _receipt(uid_not_root="pass"), _receipt(uid_not_root="pass")
    )
    assert not never_red.ok and not never_red.red_when_removed
    stays_red = iv.judge_control(
        control, _receipt(uid_not_root="fail"), _receipt(uid_not_root="fail")
    )
    assert not stays_red.ok and not stays_red.green_when_restored


def test_a_missing_check_is_never_a_pass() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "run_as_root")
    assert not iv.judge_control(control, _receipt(other="fail"), _receipt(other="pass")).ok
    assert iv.baseline_verdict(_receipt(uid_not_root="pass")) != []
    assert iv.baseline_verdict(None) == list(iv.BASELINE_CHECKS)


def test_host_pid_needs_a_refusal_and_a_passing_restore() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "host_pid")
    ok_restored = _receipt(uid_not_root="pass")
    assert iv.judge_control(control, None, ok_restored).ok
    assert not iv.judge_control(control, _receipt(uid_not_root="pass"), ok_restored).ok


def test_the_canary_pod_carries_only_synthetic_secret_references() -> None:
    pod = iv.canary_pod(name="c", namespace="n", image=_IMAGE, canary_secret="qa-isolation-canary")
    env = pod["spec"]["containers"][0]["env"][0]
    assert "value" not in env
    assert env["valueFrom"]["secretKeyRef"]["name"] == "qa-isolation-canary"
