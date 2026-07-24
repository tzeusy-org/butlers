"""bu-27dxl.5.3: cross-butler-delegation shared skill/guidance reachability.

Verifies the guidance surface for the four delegation tools
(delegate_ask/receive/answer/wake) reaches every non-staffer roster: via the
shared skill symlink pattern for most butlers, and via a Travel-local
standalone copy (Travel does not `@include` roster/shared/AGENTS.md, so it
cannot inherit the shared BUTLER_SKILLS.md-independent skill list). Staffers
(messenger, qa, switchboard) must NOT gain the skill directory — the
delegation tools are excluded for them (bu-27dxl.5.2's admission boundary).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.core.skills import process_system_prompt_base

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ROSTER_DIR = REPO_ROOT / "roster"
SHARED_SKILL = ROSTER_DIR / "shared" / "skills" / "cross-butler-delegation"

# Non-staffer rosters that symlink the shared skill (mirrors butler-memory /
# butler-notifications / routed-message-safety). Excludes travel (local copy)
# and chronicler (no .agents/skills directory at all — pre-existing gap,
# tracked separately, not introduced by this change).
SYMLINKED_ROSTERS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "relationship",
)

STAFFER_ROSTERS = ("messenger", "qa", "switchboard")


def test_shared_skill_exists() -> None:
    skill_md = SHARED_SKILL / "SKILL.md"
    assert skill_md.is_file(), f"Expected shared skill at {skill_md}"
    assert "delegate_ask" in skill_md.read_text(encoding="utf-8")


def test_butler_skills_md_mentions_delegation() -> None:
    content = (ROSTER_DIR / "shared" / "BUTLER_SKILLS.md").read_text(encoding="utf-8")
    assert "cross-butler-delegation" in content


@pytest.mark.parametrize("butler", SYMLINKED_ROSTERS)
def test_roster_symlinks_shared_skill(butler: str) -> None:
    link = ROSTER_DIR / butler / ".agents" / "skills" / "cross-butler-delegation"
    assert link.is_symlink(), f"Expected {link} to be a symlink to the shared skill"
    assert link.resolve() == SHARED_SKILL.resolve()
    assert (link / "SKILL.md").is_file()


def test_travel_has_local_non_shared_copy() -> None:
    local = ROSTER_DIR / "travel" / ".agents" / "skills" / "cross-butler-delegation"
    skill_md = local / "SKILL.md"
    assert skill_md.is_file()
    assert not local.is_symlink(), "Travel's copy must be a local file, not a shared symlink"
    assert "delegate_ask" in skill_md.read_text(encoding="utf-8")

    # Travel's own AGENTS.md does not `@include ../shared/AGENTS.md`, so the
    # mention must live directly in Travel's own guidance text.
    agents_md = (ROSTER_DIR / "travel" / "AGENTS.md").read_text(encoding="utf-8")
    assert "@../shared/AGENTS.md" not in agents_md
    assert "cross-butler-delegation" in agents_md


@pytest.mark.parametrize("staffer", STAFFER_ROSTERS)
def test_staffer_rosters_do_not_gain_delegation_skill(staffer: str) -> None:
    link = ROSTER_DIR / staffer / ".agents" / "skills" / "cross-butler-delegation"
    assert not link.exists(), (
        f"{staffer} is a staffer roster; delegate_* tools are excluded for staffers "
        "(bu-27dxl.5.2 admission boundary) and it must not carry the delegation skill"
    )


def test_finance_resolved_system_prompt_surfaces_delegation_guidance() -> None:
    """End-to-end: the actual prompt-assembly pipeline surfaces the mention.

    Exercises process_system_prompt_base (the same function read_system_prompt
    uses) against Finance's real on-disk CLAUDE.md, proving the guidance
    reaches the resolved system prompt, not just the source files.
    """
    finance_dir = ROSTER_DIR / "finance"
    base_content = (finance_dir / "CLAUDE.md").read_text(encoding="utf-8")
    resolved = process_system_prompt_base(base_content, finance_dir)
    assert "cross-butler-delegation" in resolved
