"""Contracts for runtime CLI tools shipped in the base container image."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _dockerfile_base_text() -> str:
    return Path("Dockerfile.base").read_text(encoding="utf-8")


def test_base_image_installs_uv_git_and_gh_for_qa_runtime() -> None:
    text = _dockerfile_base_text()
    assert "git" in text
    assert "uv/install.sh" in text
    assert "gh" in text
