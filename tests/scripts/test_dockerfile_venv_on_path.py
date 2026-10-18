"""The app image puts the project venv on PATH so ``kubectl exec -- butlers`` works (bu-ejcixc)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VENV_BIN = "/app/.venv/bin"


def _path_env_lines(dockerfile: str) -> list[str]:
    text = Path(dockerfile).read_text(encoding="utf-8").replace("\\\n", " ")
    return [ln.strip() for ln in text.splitlines() if re.match(r"\s*ENV\s+PATH[= ]", ln)]


def test_app_image_puts_the_venv_bin_on_path() -> None:
    lines = _path_env_lines("Dockerfile")
    assert any(_VENV_BIN in ln for ln in lines), lines


def test_venv_bin_is_appended_so_existing_tools_keep_resolving() -> None:
    (line,) = [ln for ln in _path_env_lines("Dockerfile") if _VENV_BIN in ln]
    value = line.split("PATH", 1)[1].lstrip("= ").strip().strip('"')
    parts = value.split(":")
    assert parts[0] == "$PATH"
    assert parts[-1] == _VENV_BIN


def test_path_is_set_after_the_venv_is_built() -> None:
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert text.index("ENV PATH=") > text.rindex("uv sync --frozen --no-dev ${EXTRAS")


def test_kubernetes_docs_name_the_exec_form() -> None:
    docs = Path("docs/operations/kubernetes-deployment.md").read_text(encoding="utf-8")
    assert "exec deploy/dashboard-api -- butlers" in docs
