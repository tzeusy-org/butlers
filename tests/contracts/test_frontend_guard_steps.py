"""The local frontend guard runner stays in step with the hosted `guards` job (bu-5n9pfa)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
_START = "- name: Set up Node 24\n        if: needs.route.outputs.frontend == 'true'"
_END = "- name: Bind the single frontend build"
# The frontend steps of the hosted `guards` job, up to the build-evidence seal.
CI = _CI.split(_START, 1)[1].split(_END, 1)[0]


def _runner():
    spec = importlib.util.spec_from_file_location(
        "frontend_guards", ROOT / "scripts/frontend_guards.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hosted_npm_scripts() -> list[str]:
    """Every `npm run <script>` the hosted frontend lint steps execute, in file order."""
    return re.findall(r"^\s+(?:run: |)npm run (lint[\w:-]*)\s*$", CI, flags=re.MULTILINE)


@pytest.mark.unit
def test_every_hosted_frontend_lint_script_is_run_locally() -> None:
    local = {script for _, script in _runner().STEPS}
    # `npm run lint` already chains lint:visual-role-css (pinned below).
    assert set(_hosted_npm_scripts()) - {"lint:visual-role-css"} == local
    import json

    lint = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]["lint"]
    assert "lint:visual-role-css" in lint


@pytest.mark.unit
def test_hosted_only_steps_are_exactly_the_ones_not_in_the_local_list() -> None:
    runner = _runner()
    hosted = set(re.findall(r"^\s+(?:run: |)npm run (\w[\w:-]*)\s*$", CI, flags=re.MULTILINE))
    local = {script for _, script in runner.STEPS}
    assert hosted - local - {"lint:visual-role-css"} == set(runner.HOSTED_ONLY)


@pytest.mark.unit
def test_every_local_step_is_a_real_npm_script() -> None:
    import json

    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    assert all(script in scripts for _, script in _runner().STEPS)


@pytest.mark.unit
def test_make_check_guards_and_pre_push_both_invoke_the_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    guards = makefile.split("\ncheck-guards:", 1)[1].split("\n\ncheck:", 1)[0]
    assert "scripts/frontend_guards.py" in guards
    pre_push = (ROOT / "scripts/pre_push.py").read_text(encoding="utf-8")
    assert "scripts/frontend_guards.py" in pre_push
