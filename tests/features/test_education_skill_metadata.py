"""Regression tests for education skill metadata."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SKILLS_DIR = Path(__file__).resolve().parents[2] / "roster" / "education" / ".agents" / "skills"


def _front_matter_lines(skill_path: Path) -> list[str]:
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[:1] == ["---"], f"{skill_path} is missing YAML front matter"

    try:
        closing_index = lines.index("---", 1)
    except ValueError as exc:  # pragma: no cover - defensive failure path
        raise AssertionError(f"{skill_path} has an unterminated YAML front matter block") from exc

    return lines[1:closing_index]


def test_education_skills_define_required_front_matter() -> None:
    """Education-local skills must include the metadata block Codex expects."""
    skill_paths = sorted(_SKILLS_DIR.glob("*/SKILL.md"))
    assert skill_paths, "Expected at least one education skill"

    for skill_path in skill_paths:
        front_matter = _front_matter_lines(skill_path)
        data: dict[str, str] = {}
        for line in front_matter:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()

        assert data.get("name") == skill_path.parent.name
        assert data.get("description"), f"{skill_path} is missing a description"
