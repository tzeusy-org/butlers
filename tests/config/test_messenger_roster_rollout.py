"""Regression tests for messenger roster scaffolding and runbook coverage."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
MESSENGER_DIR = REPO_ROOT / "roster" / "messenger"


def test_messenger_roster_files_and_config() -> None:
    """Identity files exist; butler.toml wired correctly; guidance has required contracts."""
    assert (MESSENGER_DIR / "butler.toml").is_file()
    assert (MESSENGER_DIR / "CLAUDE.md").is_file()
    assert (MESSENGER_DIR / "MANIFESTO.md").is_file()

    with (MESSENGER_DIR / "butler.toml").open("rb") as fh:
        data = tomllib.load(fh)
    butler = data.get("butler", {})
    assert butler.get("name") == "messenger" and butler.get("port") == 41104
    modules = data.get("modules", {})
    assert isinstance(modules.get("telegram"), dict) and isinstance(modules.get("email"), dict)

    guidance = (MESSENGER_DIR / "AGENTS.md").read_text().lower()
    for frag in ("route.execute", "notify.v1", "must not recursively call `notify`"):
        assert frag in guidance

    readme = (REPO_ROOT / "README.md").read_text().lower()
    assert "butlers up --only switchboard --only messenger" in readme
    assert "| 41104" in readme
