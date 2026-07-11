"""Regression test for the roster CLAUDE.md/AGENTS.md unification (bu-1mq1d).

Every butler's `CLAUDE.md` must be a bare `@AGENTS.md` include line — see
openspec/specs/butler-base-spec/spec.md "CLAUDE.md as System Prompt Entry
Point". Butler-specific personality/instructions live in AGENTS.md so a single
prompt composes for every runtime (Claude reads CLAUDE.md, Codex/others read
AGENTS.md directly). Before bu-1mq1d.1 (#3110), five butlers had CLAUDE.md and
AGENTS.md bodies that had silently forked; this test guards against that
recurring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_INCLUDE = "@AGENTS.md"

# Butlers whose CLAUDE.md is intentionally NOT a bare "@AGENTS.md" include.
# Empty on purpose: as of bu-1mq1d.1 (#3110) every roster butler was unified
# onto the include pattern. Add an entry here only for a deliberate, reviewed
# exception, with a comment explaining why that butler's CLAUDE.md must carry
# its own body instead of delegating to AGENTS.md.
ALLOWLISTED_DIVERGENT_BUTLERS: frozenset[str] = frozenset()


def _roster_claude_md_paths() -> list[Path]:
    return sorted(REPO_ROOT.glob("roster/*/CLAUDE.md"))


def test_roster_has_claude_md_files() -> None:
    """Sanity check that the glob is actually finding the roster's CLAUDE.md files."""
    paths = _roster_claude_md_paths()
    assert len(paths) >= 10, f"expected at least 10 roster CLAUDE.md files, found {len(paths)}"


def test_roster_claude_md_is_bare_agents_include() -> None:
    """Every non-allowlisted roster CLAUDE.md must equal the canonical `@AGENTS.md` include."""
    offenders: list[str] = []
    for path in _roster_claude_md_paths():
        butler_name = path.parent.name
        if butler_name in ALLOWLISTED_DIVERGENT_BUTLERS:
            continue
        content = path.read_text().strip()
        if content != CANONICAL_INCLUDE:
            offenders.append(f"roster/{butler_name}/CLAUDE.md")

    assert not offenders, (
        "Roster CLAUDE.md files must be a bare '@AGENTS.md' include (butler-specific "
        "content belongs in AGENTS.md). Divergent files: "
        f"{offenders}. Either move the extra content into the butler's AGENTS.md, or "
        "add the butler to ALLOWLISTED_DIVERGENT_BUTLERS with a comment justifying the "
        "exception."
    )
