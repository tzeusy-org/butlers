"""scripts/k8s/deploy-dev.sh: one-command butlers-dev deploy (bu-dr23fc)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $* @ $(pwd)" >> "$CALL_LOG"
{extra}
"""


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A repo-shaped tree holding the real scripts plus stub tools on PATH."""
    (tmp_path / "scripts" / "k8s").mkdir(parents=True)
    (tmp_path / "deploy" / "helm" / "butlers").mkdir(parents=True)
    for rel in ("scripts/k8s/deploy-dev.sh", "scripts/site-env.sh"):
        shutil.copy2(_REPO_ROOT / rel, tmp_path / rel)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "make": "",
        "kubectl": "",
        "git": 'case "$*" in *branch*) echo main ;; esac',
        # Run the wrapped command so the inner `make` is logged too.
        "bws": 'while [ "$1" != "--" ]; do shift; done; shift; "$@"',
    }
    for name, extra in stubs.items():
        stub = bin_dir / name
        stub.write_text(_STUB.format(extra=extra), encoding="utf-8")
        stub.chmod(0o755)
    return tmp_path


def _run(
    repo: Path, *args: str, site: bool = True, bws: bool = True, default_bws: bool = False
) -> tuple[int, str, str]:
    site_env = repo / "site.env"
    if site:
        site_env.write_text(
            "BUTLERS_PUBLIC_HOST=host.example\nBUTLERS_IMAGE_REGISTRY=registry.example\n",
            encoding="utf-8",
        )
    # HOME is the fake repo, so the default BWS env file is <repo>/.secrets/.bws.dev.env.
    bws_env = repo / ".secrets" / ".bws.dev.env" if default_bws else repo / "bws.env"
    if bws:
        bws_env.parent.mkdir(exist_ok=True)
        bws_env.write_text("BWS_PROJECT_ID=proj-fixture\n", encoding="utf-8")
    log = repo / "calls.log"
    log.touch()
    env = {
        "PATH": f"{repo / 'bin'}:{os.environ['PATH']}",
        "HOME": str(repo),
        "CALL_LOG": str(log),
        "BUTLERS_SITE_ENV": str(site_env),
    }
    if not default_bws:
        env["BWS_ENV_FILE"] = str(bws_env)
    result = subprocess.run(
        ["bash", str(repo / "scripts" / "k8s" / "deploy-dev.sh"), *args],
        capture_output=True, text=True, env=env, cwd=repo,
    )  # fmt: skip
    return result.returncode, result.stderr, log.read_text(encoding="utf-8")


def test_ships_head_under_bws_then_reports_status(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo)
    assert code == 0, stderr
    chart = fake_repo / "deploy" / "helm" / "butlers"
    lines = [line for line in calls.splitlines() if not line.startswith("git ")]
    assert lines[0] == f"bws run --project-id proj-fixture -- make ship-dev @ {chart}"
    assert lines[1] == f"make ship-dev @ {chart}"
    assert f"make status-dev @ {chart}" in lines
    assert "secrets-dev" not in calls


def test_bws_env_file_defaults_to_home_secrets(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, default_bws=True)
    assert code == 0, stderr
    assert "bws run --project-id proj-fixture -- make ship-dev" in calls


def test_tag_argument_redeploys_without_building(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, "abc123def456")
    assert code == 0, stderr
    assert "make deploy-dev TAG=abc123def456" in calls
    assert "ship-dev" not in calls
    assert "bws " not in calls


def test_refuses_without_site_env_and_touches_nothing(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, site=False)
    assert code != 0
    assert "site env" in stderr
    assert "make " not in calls


def test_refuses_without_bws_env_file_when_building(fake_repo: Path) -> None:
    code, stderr, calls = _run(fake_repo, bws=False)
    assert code != 0
    assert "bws.env" in stderr
    assert "make " not in calls
