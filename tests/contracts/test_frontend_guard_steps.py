"""The local frontend guard runner stays in step with the hosted `guards` job (bu-5n9pfa)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
_GUARDS = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"][
    "guards"
]["steps"]
# Every command the hosted guards job runs in frontend/.
HOSTED_COMMANDS = [
    line.strip()
    for step in _GUARDS
    if step.get("working-directory") == "frontend"
    for line in step["run"].splitlines()
    if line.strip()
]
# Hosted commands the runner deliberately does not run (`npm run lint` chains visual-role-css).
_NOT_LOCAL = {
    "npm ci",
    "npm run lint:visual-role-css",
    "node --test --test-timeout=300000 scripts/worktree-tooling-contract.mjs"
    " route-a-api-receipt.test.mjs",
}


def _runner():
    spec = importlib.util.spec_from_file_location(
        "frontend_guards", ROOT / "scripts/frontend_guards.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_every_hosted_frontend_command_is_run_locally_or_declared_hosted_only() -> None:
    runner = _runner()
    local = {f"npm run {script}" for _, script in runner.STEPS}
    hosted_only = {f"npm run {script}" for script in runner.HOSTED_ONLY}
    assert set(HOSTED_COMMANDS) == local | hosted_only | _NOT_LOCAL
    # `npm run lint` chains lint:visual-role-css, which is why it is not a local step of its own.
    lint = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]["lint"]
    assert "lint:visual-role-css" in lint


@pytest.mark.unit
def test_every_local_step_is_a_real_npm_script() -> None:
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    assert all(script in scripts for _, script in _runner().STEPS)


@pytest.mark.unit
def test_make_check_guards_invokes_the_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    guards = makefile.split("\ncheck-guards:", 1)[1].split("\n\ncheck:", 1)[0]
    assert "scripts/frontend_guards.py" in guards
