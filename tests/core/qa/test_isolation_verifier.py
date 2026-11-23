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


def test_the_probe_job_is_the_production_template_with_only_the_command_and_containment_label_changed() -> (
    None
):
    probe = _job()
    prod = build_investigator_job(
        attempt_id=uuid.UUID(int=1),
        release="butlers",
        namespace="butlers-dev",
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        command=["x"],
        deadline_s=300,
        containment="probe",  # the one label the probe changes, so the sweep ignores it
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


def _receipt(**outcomes):
    return {
        "passed": all(v == "pass" for v in outcomes.values()),
        "checks": [{"check": k, "outcome": v} for k, v in outcomes.items()],
    }


def test_a_control_needs_red_when_removed_and_pass_when_restored() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "run_as_root")
    good = iv.judge_control(control, _receipt(uid_not_root="fail"), _receipt(uid_not_root="pass"))
    assert good.ok and good.caught_by == "probe"
    never_red = iv.judge_control(
        control, _receipt(uid_not_root="pass"), _receipt(uid_not_root="pass")
    )
    assert not never_red.ok and never_red.caught_by is None
    stays_red = iv.judge_control(
        control, _receipt(uid_not_root="fail"), _receipt(uid_not_root="fail")
    )
    assert not stays_red.ok and not stays_red.green_when_restored


def test_an_admission_refusal_is_red_for_every_control_and_records_the_layer() -> None:
    for control in iv.INVERSE_CONTROLS:
        restored = _receipt(**{control.expect: "pass"})
        verdict = iv.judge_control(control, None, restored, removed_refused=True)
        assert verdict.ok and verdict.caught_by == "admission", control.name


def test_a_missing_check_or_receipt_is_never_a_catch() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "run_as_root")
    assert not iv.judge_control(control, _receipt(other="fail"), _receipt(other="pass")).ok
    assert not iv.judge_control(control, None, _receipt(uid_not_root="pass")).ok
    assert iv.baseline_verdict(_receipt(uid_not_root="pass")) != []
    assert iv.baseline_verdict(None) == list(iv.BASELINE_CHECKS)


def test_probe_jobs_carry_the_probe_containment_label_so_the_sweep_ignores_them() -> None:
    job = _job()
    assert job["metadata"]["labels"]["butlers.io/qa-containment"] == "probe"


def test_the_canary_pod_carries_only_synthetic_secret_references() -> None:
    pod = iv.canary_pod(name="c", namespace="n", image=_IMAGE, canary_secret="qa-isolation-canary")
    env = pod["spec"]["containers"][0]["env"][0]
    assert "value" not in env
    assert env["valueFrom"]["secretKeyRef"]["name"] == "qa-isolation-canary"


def test_a_dry_run_passes_only_when_the_probe_is_admitted_and_every_control_refused() -> None:
    every = {c.name: True for c in iv.INVERSE_CONTROLS}
    assert iv.judge_dry_run(policies_valid=True, probe_admitted=True, refused=every).ok
    assert iv.judge_dry_run(policies_valid=None, probe_admitted=True, refused=every).ok
    assert not iv.judge_dry_run(policies_valid=False, probe_admitted=True, refused=every).ok
    assert not iv.judge_dry_run(policies_valid=True, probe_admitted=False, refused=every).ok
    one_open = {**every, "host_pid": False}
    verdict = iv.judge_dry_run(policies_valid=True, probe_admitted=True, refused=one_open)
    assert verdict.unrefused_controls == ("host_pid",) and not verdict.ok
    assert not iv.judge_dry_run(policies_valid=True, probe_admitted=True, refused={}).ok


def test_a_restored_probe_that_fails_any_other_check_is_not_green() -> None:
    control = next(c for c in iv.INVERSE_CONTROLS if c.name == "run_as_root")
    restored = _receipt(uid_not_root="pass", tcp_refused_tracker="fail")
    verdict = iv.judge_control(control, _receipt(uid_not_root="fail"), restored)
    assert verdict.red_when_removed and not verdict.green_when_restored and not verdict.ok


def test_the_baseline_names_every_failing_check_not_only_the_core_ones() -> None:
    receipt = _receipt(**{c: "pass" for c in iv.BASELINE_CHECKS}, **{"tcp_refused:tracker": "fail"})
    assert iv.baseline_verdict(receipt) == ["tcp_refused:tracker"]


def test_probe_command_carries_the_recheck_delay_only_when_asked() -> None:
    assert "--recheck-after" not in iv.probe_command()
    command = iv.probe_command(recheck_after=20)
    assert command[command.index("--recheck-after") + 1] == "20"
