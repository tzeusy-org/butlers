"""Contract tests for Codex-discoverable SKILL.md frontmatter."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _discover_skill_markdowns() -> list[Path]:
    paths = sorted(_REPO_ROOT.glob("roster/*/.agents/skills/*/SKILL.md"))
    paths.extend(sorted(_REPO_ROOT.glob("roster/shared/skills/*/SKILL.md")))
    paths.extend(sorted(_REPO_ROOT.glob(".codex/skills/*/SKILL.md")))
    return paths


def test_all_discoverable_skill_markdowns_have_yaml_frontmatter() -> None:
    """Codex-discoverable skills must start with YAML frontmatter delimiters."""
    skill_paths = _discover_skill_markdowns()
    assert skill_paths, "Expected repository-managed SKILL.md files to exist"

    invalid_paths: list[str] = []
    for path in skill_paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 3 or lines[0] != "---" or "---" not in lines[1:]:
            invalid_paths.append(str(path.relative_to(_REPO_ROOT)))

    assert not invalid_paths, "Missing YAML frontmatter in: " + ", ".join(invalid_paths)
