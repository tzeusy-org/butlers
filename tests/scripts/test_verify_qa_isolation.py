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


def test_dry_run_sends_every_manifest_server_side_and_creates_nothing(monkeypatch, capsys) -> None:
    module = _load()
    calls: list[list[str]] = []

    class _Done:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append(command)
        done = _Done()
        # Admit the probe (first call), refuse every mutated Job after it.
        done.returncode = 0 if len(calls) == 1 else 1
        return done

    monkeypatch.setattr(module, "_kubectl", lambda *a, **k: "k3d-dev\n")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main(["dry-run", "--namespace", "butlers-dev", "--image", _IMAGE]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["passed"] is True and out["unrefused_controls"] == []
    assert calls and all("--dry-run=server" in c for c in calls)
    assert all(c[1].startswith("--as=system:serviceaccount:butlers-dev:") for c in calls)
