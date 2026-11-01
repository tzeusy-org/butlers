"""All-roster core QA guidance; retired module configuration and skill stay absent."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import load_config

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ROSTER_DIR = REPO_ROOT / "roster"
SHARED_SKILL = ROSTER_DIR / "shared" / "skills" / "self-healing"

ROSTERS_WITH_BUTLER_TOML = tuple(
    sorted(d.name for d in ROSTER_DIR.iterdir() if (d / "butler.toml").is_file())
)


def test_shared_skill_exists() -> None:
    skill_md = SHARED_SKILL / "SKILL.md"
    assert not skill_md.exists()
    content = (ROSTER_DIR / "shared" / "BUTLER_SKILLS.md").read_text(encoding="utf-8")
    assert "report_error" in content
    assert "get_healing_status" in content


def test_butler_skills_md_mentions_self_healing_conditionally() -> None:
    content = (ROSTER_DIR / "shared" / "BUTLER_SKILLS.md").read_text(encoding="utf-8")
    assert "core QA relay" in content
    assert "Only present when the `self_healing` module is enabled" not in content


@pytest.mark.parametrize("butler", ROSTERS_WITH_BUTLER_TOML)
def test_roster_enabling_self_healing_module_carries_the_skill(butler: str) -> None:
    """Every original roster keeps core relay access without a module/skill opt-in."""
    config = load_config(ROSTER_DIR / butler)
    assert "self_healing" not in config.modules
    link = ROSTER_DIR / butler / ".agents" / "skills" / "self-healing"
    assert not link.is_symlink()
    assert not link.exists()


def test_every_roster_carries_self_healing_skill() -> None:
    """No roster carries the retired skill; the core tools remain universal."""
    linked = [
        butler
        for butler in ROSTERS_WITH_BUTLER_TOML
        if (ROSTER_DIR / butler / ".agents" / "skills" / "self-healing").exists()
    ]
    assert linked == []
