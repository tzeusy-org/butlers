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
