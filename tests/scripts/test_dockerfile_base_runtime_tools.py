"""Contracts for runtime CLI tools shipped in the base container image."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _dockerfile_base_text() -> str:
    return Path("Dockerfile.base").read_text(encoding="utf-8")


def _compose_script_text() -> str:
    return Path("scripts/compose.sh").read_text(encoding="utf-8")


def test_base_image_installs_uv_git_and_gh_for_qa_runtime() -> None:
    text = _dockerfile_base_text()
    assert "git" in text
    assert "python -m pip install --no-cache-dir uv" in text
    assert "uv --version" in text
    assert "gh" in text


def test_compose_base_freshness_uses_pinned_dockerfile_not_live_npm_latest() -> None:
    dockerfile_text = _dockerfile_base_text()
    compose_text = _compose_script_text()
    runtime_cli_packages = [
        "@anthropic-ai/claude-code",
        "@google/gemini-cli",
        "@openai/codex",
        "opencode-ai",
    ]

    for package in runtime_cli_packages:
        assert re.search(rf"{re.escape(package)}@\d+\.\d+\.\d+", dockerfile_text)

    assert "registry.npmjs.org/${pkg}/latest" not in compose_text
    assert "CLI_PKGS=" not in compose_text
