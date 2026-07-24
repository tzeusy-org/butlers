"""bu-402cy: self-healing shared skill claim/symlink consistency guard.

`roster/shared/BUTLER_SKILLS.md` advertises a `self-healing` shared skill in
every butler's system prompt (the shared file is appended to all rosters —
see `src/butlers/core/skills.py`). The `self_healing` module is opt-in per
butler via `[modules.self_healing]` in `butler.toml` (by design — see
`openspec/changes/archive/2026-03-18-butler-self-healing/design.md`), and no
roster currently symlinks the shared skill into its `.agents/skills/`
directory, so no butler can actually read it even if it did enable the
module. The `BUTLER_SKILLS.md` entry is worded conditionally ("Only present
when the `self_healing` module is enabled ... for your butler"), mirroring
the established `cross-butler-delegation` pattern (see
`test_delegation_guidance_reachability.py`).

This test guards the claim↔reality gap going forward: it does not require
today's zero rosters to carry the symlink, but if any roster's `butler.toml`
ever gains `[modules.self_healing]`, that roster must also carry the shared
skill symlink — otherwise the module's own skill becomes unreachable again.
"""

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
    if "self_healing" not in config.modules:
        pytest.skip(f"{butler} does not enable [modules.self_healing]")

    link = ROSTER_DIR / butler / ".agents" / "skills" / "self-healing"
    assert link.is_symlink(), (
        f"{butler} enables [modules.self_healing] but does not symlink the "
        f"shared self-healing skill at {link}"
    )
    assert link.resolve() == SHARED_SKILL.resolve()
    assert (link / "SKILL.md").is_file()


def test_no_roster_currently_symlinks_self_healing_skill() -> None:
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
    assert linked == []
