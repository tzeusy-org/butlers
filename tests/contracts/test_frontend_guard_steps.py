"""The local frontend guard runner stays in step with the hosted `guards` job (bu-5n9pfa)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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
# Hosted commands the runner deliberately does not run.
_NOT_LOCAL = {
    "npm ci",
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


@pytest.mark.unit
def test_every_local_step_is_a_real_npm_script() -> None:
    scripts = json.loads((ROOT / "frontend/package.json").read_text())["scripts"]
    assert all(script in scripts for _, script in _runner().STEPS)


@pytest.mark.unit
def test_make_check_guards_passes_the_configured_guards_base(tmp_path) -> None:
    # Execute the real recipe with recording tools; skip unrelated prerequisites.
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "python-arguments"
    for name, body in {
        "python3": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$ARGUMENT_LOG"\n',
        "git": '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$ARGUMENT_LOG"\n',
        "openspec": "#!/bin/sh\nexit 0\n",
    }.items():
        tool = tools / name
        tool.write_text(body)
        tool.chmod(0o755)
    prerequisites = (
        "check-lock",
        "lint",
        "check-format",
        "check-for-update-joins",
        "check-em-dashes",
        "check-spec-overwrites",
        "check-countable-tasks",
        "check-duplicate-names",
        "check-session-links",
    )
    subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(ROOT / "Makefile"),
            *(f"--assume-old={name}" for name in prerequisites),
            "check-guards",
            "GUARDS_BASE=review-fork",
            "BASE=unrelated-base",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{tools}:{os.defpath}", "ARGUMENT_LOG": str(log)},
        check=True,
        capture_output=True,
    )
    commands = log.read_text().splitlines()
    assert "scripts/frontend_guards.py --base review-fork" in commands
    assert not any("extract-frontend-copy.py" in command for command in commands)
    assert not any("COPY_INVENTORY.md" in command for command in commands)
