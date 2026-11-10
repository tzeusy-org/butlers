"""Exercise the actual publication script with inert Docker/Git command fixtures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "repo"
    scripts = root / "scripts"
    (scripts / "k8s").mkdir(parents=True)
    shutil.copy2("scripts/k8s/build-push.sh", scripts / "k8s/build-push.sh")
    (scripts / "site-env.sh").write_text("# inert owned site fixture\n")
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "docker.jsonl"
    python = os.fspath(Path(os.sys.executable).resolve())
    (commands / "git").write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  "rev-parse --short=12 HEAD") echo aaaaaaaaaaaa;;\n'
        '  "rev-parse HEAD") echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;;\n'
        '  "status --porcelain --untracked-files=no") :;;\n'
        "  *) exit 91;;\nesac\n"
    )
    (commands / "docker").write_text(
        f"#!{python}\nimport json,sys\n"
        f"with open({str(log)!r}, 'a') as stream: "
        "stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
    )
    for path in commands.iterdir():
        path.chmod(0o755)
    context = tmp_path / "closed"
    context.mkdir()
    (context / "provenance.json").write_text("{}\n")
    go_context = tmp_path / "go-closed"
    go_context.mkdir()
    (go_context / "provenance.json").write_text("{}\n")
    environment = {
        "PATH": f"{commands}:/usr/bin:/bin",
        "BUTLERS_IMAGE_REGISTRY": "registry.test/team",
        "BUTLERS_BASE_IMAGE": "registry.test/team/base@sha256:" + "b" * 64,
        "BUTLERS_PYTHON_ARTIFACTS": str(context),
        "BUTLERS_GO_ARTIFACTS": str(go_context),
    }
    return root, log, context, go_context, environment


@pytest.mark.parametrize("mode", ["dev", "prod"])
def test_actual_app_build_consumes_closed_inputs_and_preserves_frontend_publication(launcher, mode):
    root, log, context, go_context, environment = launcher
    result = subprocess.run(
        ["bash", "scripts/k8s/build-push.sh", mode],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 4
    app, push_app, frontend, push_frontend = calls
    assert app == [
        "build",
        "--build-arg",
        "GIT_SHA=" + "a" * 40,
        "--build-arg",
        "BUTLERS_BASE_IMAGE=" + environment["BUTLERS_BASE_IMAGE"],
        "--build-context",
        f"python-artifacts={context}",
        "--build-context",
        f"go-artifacts={go_context}",
        "--target",
        "production",
        "-t",
        "registry.test/team/butlers-app:aaaaaaaaaaaa",
        ".",
    ]
    assert push_app == ["push", "registry.test/team/butlers-app:aaaaaaaaaaaa"]
    assert frontend[0:3] == ["build", "-f", "Dockerfile.frontend"]
    assert f"registry.test/team/butlers-frontend:aaaaaaaaaaaa-{mode}" in frontend
    assert push_frontend == ["push", f"registry.test/team/butlers-frontend:aaaaaaaaaaaa-{mode}"]
    assert result.stdout.rstrip().splitlines()[-1] == "aaaaaaaaaaaa"


@pytest.mark.parametrize(
    "failure",
    ["missing-base", "mutable-base", "missing-context", "symlink", "missing-go", "symlink-go"],
)
def test_bad_closed_inputs_refuse_before_any_docker_boundary(launcher, failure):
    root, log, context, go_context, environment = launcher
    if failure == "missing-base":
        environment.pop("BUTLERS_BASE_IMAGE")
    elif failure == "mutable-base":
        environment["BUTLERS_BASE_IMAGE"] = "registry.test/team/base:latest"
    elif failure == "missing-context":
        environment.pop("BUTLERS_PYTHON_ARTIFACTS")
    elif failure == "missing-go":
        environment.pop("BUTLERS_GO_ARTIFACTS")
    elif failure == "symlink-go":
        alias = go_context.with_name("go-alias")
        alias.symlink_to(go_context, target_is_directory=True)
        environment["BUTLERS_GO_ARTIFACTS"] = str(alias)
    else:
        alias = context.with_name("alias")
        alias.symlink_to(context, target_is_directory=True)
        environment["BUTLERS_PYTHON_ARTIFACTS"] = str(alias)
    result = subprocess.run(
        ["bash", "scripts/k8s/build-push.sh", "dev"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "ERROR:" in result.stderr
    assert not log.exists()
