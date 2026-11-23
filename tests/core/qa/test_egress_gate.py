"""bu-ejow5p.11: the start-up gate runs nothing until the egress policy refuses the API server."""

from __future__ import annotations

import pytest

from butlers.core.qa import egress_gate as gate
from butlers.core.qa.investigator_job import build_investigator_job, gated_command

pytestmark = pytest.mark.unit

_TARGET = ("10.43.0.1", 443)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(answers, *, deadline_s=30, consecutive=3) -> bool:
    clock = _Clock()
    seq = iter(answers)
    return gate.wait_until_enforced(
        _TARGET,
        deadline_s=deadline_s,
        consecutive=consecutive,
        interval_s=1.0,
        reachable=lambda _h, _p: next(seq, True),
        clock=clock,
        sleep=clock.sleep,
    )


def test_a_policy_that_takes_a_while_is_waited_for() -> None:
    # Reachable for the first 8 attempts (policy not programmed yet), then refused.
    assert _wait([True] * 8 + [False] * 3)


def test_the_refusals_must_be_consecutive() -> None:
    # A single refused attempt between reachable ones is not enforcement.
    assert not _wait([False, False, True] * 20, deadline_s=30)


def test_a_policy_that_never_arrives_fails_closed_at_the_deadline() -> None:
    assert not _wait([True] * 1000, deadline_s=10)


def test_the_target_is_the_pods_own_api_server_and_missing_means_no_target() -> None:
    assert gate.target_from_env(
        {"KUBERNETES_SERVICE_HOST": "10.43.0.1", "KUBERNETES_SERVICE_PORT": "443"}
    ) == ("10.43.0.1", 443)
    assert gate.target_from_env({}) is None
    assert (
        gate.target_from_env({"KUBERNETES_SERVICE_HOST": "h", "KUBERNETES_SERVICE_PORT": "x"})
        is None
    )


def test_the_command_never_runs_when_the_policy_is_not_in_force(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setattr(gate, "wait_until_enforced", lambda *a, **k: False)
    monkeypatch.setattr(gate.os, "execvp", lambda *a: pytest.fail("must not exec"))
    with pytest.raises(SystemExit) as stop:
        gate.main(["--", "echo", "hi"])
    assert stop.value.code == gate.EXIT_NOT_ENFORCED
    err = capsys.readouterr().err
    assert err.strip() == gate.REASON_NOT_ENFORCED and "10.43" not in err


def test_no_target_fails_closed_with_its_own_reason(monkeypatch, capsys) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(gate.os, "execvp", lambda *a: pytest.fail("must not exec"))
    with pytest.raises(SystemExit) as stop:
        gate.main(["--", "echo", "hi"])
    assert stop.value.code == gate.EXIT_NOT_ENFORCED
    assert capsys.readouterr().err.strip() == gate.REASON_NO_TARGET


def test_once_enforced_the_real_command_is_exec_ed_in_place(monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    monkeypatch.setattr(gate, "wait_until_enforced", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(gate.os, "execvp", lambda f, argv: seen.append((f, list(argv))))
    gate.main(["--", "agent", "--flag"])
    assert seen == [("agent", ["agent", "--flag"])]


def test_a_missing_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as stop:
        gate.main(["--"])
    assert stop.value.code == 2


def test_every_investigator_job_starts_under_the_gate() -> None:
    import uuid

    job = build_investigator_job(
        attempt_id=uuid.UUID(int=1),
        release="butlers",
        namespace="n",
        image="r/i@sha256:" + "a" * 64,
        exchange_claim="c",
        command=["agent", "run"],
        deadline_s=600,
    )
    command = job["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command == gated_command(["agent", "run"])
    assert command[:5] == ["python", "-I", "-m", gate.__name__, "--"]
