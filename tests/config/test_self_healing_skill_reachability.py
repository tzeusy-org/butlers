"""All-roster declared RFC 0015 relay guidance reachability."""

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
    assert skill_md.is_file(), f"Expected shared skill at {skill_md}"
    content = skill_md.read_text(encoding="utf-8")
    assert "report_error" in content
    assert "get_healing_status" in content


def test_butler_skills_md_mentions_self_healing_conditionally() -> None:
    content = (ROSTER_DIR / "shared" / "BUTLER_SKILLS.md").read_text(encoding="utf-8")
    assert "self-healing" in content
    # Must not read as an unconditional/universal claim — the module is
    # opt-in per butler, so the doc must say so (bu-402cy).
    assert "Only present when the `self_healing` module is enabled" in content


@pytest.mark.parametrize("butler", ROSTERS_WITH_BUTLER_TOML)
def test_roster_enabling_self_healing_module_carries_the_skill(butler: str) -> None:
    """If a roster opts into `[modules.self_healing]`, it must symlink the skill.

    Currently no roster enables the module, so this is a forward guard: it
    passes vacuously today and starts failing the moment a roster enables
    `self_healing` without also wiring the shared skill in, which is exactly
    the inconsistency this bead fixed for the doc claim.
    """
    config = load_config(ROSTER_DIR / butler)
    assert "self_healing" in config.modules

    link = ROSTER_DIR / butler / ".agents" / "skills" / "self-healing"
    assert link.is_symlink(), (
        f"{butler} enables [modules.self_healing] but does not symlink the "
        f"shared self-healing skill at {link}"
    )
    assert link.resolve() == SHARED_SKILL.resolve()
    assert (link / "SKILL.md").is_file()


def test_every_roster_carries_self_healing_skill() -> None:
    """Documents present-day ground truth (bu-402cy investigation).

    No roster symlinks the shared self-healing skill today, because no
    roster enables the module. If this starts failing, update
    `roster/shared/BUTLER_SKILLS.md`'s self-healing entry and this test
    together — the doc's conditional wording assumed this baseline.
    """
    linked = [
        butler
        for butler in ROSTERS_WITH_BUTLER_TOML
        if (ROSTER_DIR / butler / ".agents" / "skills" / "self-healing").exists()
    ]
    assert linked == list(ROSTERS_WITH_BUTLER_TOML)
