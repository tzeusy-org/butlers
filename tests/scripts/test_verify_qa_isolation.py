"""bu-ejow5p.11: the verifier script renders offline and refuses non-dev namespaces."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_qa_isolation.py"
_IMAGE = "registry.invalid/butlers-app@sha256:" + "a" * 64


def _load():
    spec = importlib.util.spec_from_file_location("verify_qa_isolation", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_needs_no_cluster_and_includes_every_inverse_control(capsys) -> None:
    module = _load()
    assert module.main(["render", "--namespace", "butlers-dev", "--image", _IMAGE]) == 0
    out = json.loads(capsys.readouterr().out)
    from butlers.core.qa.isolation_verifier import INVERSE_CONTROLS

    assert set(out["inverse"]) == {c.name for c in INVERSE_CONTROLS}
    assert out["inverse"]["host_pid"]["spec"]["template"]["spec"]["hostPID"] is True


def test_a_non_dev_namespace_is_refused_before_anything_runs(capsys) -> None:
    module = _load()
    assert module.main(["run", "--namespace", "butlers-prod", "--image", _IMAGE]) == 2
    assert "only runs in a -dev namespace" in capsys.readouterr().err


def test_dry_run_refuses_a_non_dev_namespace_and_a_non_dev_context(capsys, monkeypatch) -> None:
    module = _load()
    assert module.main(["dry-run", "--namespace", "butlers-prod", "--image", _IMAGE]) == 2
    monkeypatch.setattr(module, "_kubectl", lambda *a, **k: "prod-cluster\n")
    assert module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE]) == 2
    assert "does not look like a dev cluster" in capsys.readouterr().err


_DENIAL = (
    'Error from server (Forbidden): ValidatingAdmissionPolicy "butlers-qa-investigator-job" '
    'with binding "butlers-qa-investigator-job" denied request: the pod has no host namespace'
)


def _stub(monkeypatch, module, results):
    """``results``: list of (returncode, stderr), consumed per kubectl apply call."""
    calls: list[list[str]] = []

    class _Done:
        def __init__(self, code, err):
            self.returncode, self.stderr = code, err

    def fake_run(command, **kwargs):
        calls.append(command)
        return _Done(*results[min(len(calls) - 1, len(results) - 1)])

    monkeypatch.setattr(module, "_kubectl", lambda *a, **k: "k3d-qa\n")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


def test_dry_run_sends_every_manifest_server_side_and_creates_nothing(monkeypatch, capsys) -> None:
    module = _load()
    calls = _stub(monkeypatch, module, [(0, ""), (1, _DENIAL)])
    assert module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is True and out["unrefused_controls"] == []
    assert calls and all("--dry-run=server" in c for c in calls)
    assert all(c[1].startswith("--as=system:serviceaccount:butlers-dev:") for c in calls)


def test_a_failure_that_is_not_a_policy_denial_is_an_error_never_a_catch(
    monkeypatch, capsys
) -> None:
    module = _load()
    for stderr in (
        "The connection to the server was refused",
        'Error from server (Forbidden): jobs.batch is forbidden: User "x" cannot create',
        'ValidatingAdmissionPolicy "some-other-policy" with binding "b" denied request: no',
    ):
        _stub(monkeypatch, module, [(0, ""), (1, stderr)])
        code = module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE])
        assert code == 3, stderr
        assert "not a pass" in capsys.readouterr().err


def test_an_admitted_control_is_reported_unrefused(monkeypatch, capsys) -> None:
    module = _load()
    _stub(monkeypatch, module, [(0, "")])
    assert module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is False and len(out["unrefused_controls"]) == 5


@pytest.mark.parametrize(
    ("context", "accepted"),
    [
        ("k3d-qa", True),
        ("butlers-dev", True),
        ("dev", True),
        ("arn:aws:eks:x:cluster/team-dev", True),
        ("prod-devops", False),
        ("devops", False),
        ("butlers-prod", False),
        ("k3d-prod", False),
        ("", False),
    ],
)
def test_the_context_check_matches_whole_segments_and_rejects_prod(
    monkeypatch, capsys, context, accepted
) -> None:
    module = _load()
    monkeypatch.setattr(module, "_kubectl", lambda *a, **k: context + "\n")
    monkeypatch.setattr(module, "dry_run", lambda args: {"passed": True})
    code = module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE])
    assert (code == 0) is accepted
