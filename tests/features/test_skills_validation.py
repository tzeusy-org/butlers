"""Tests for skill directory name validation (kebab-case)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from butlers.core.skills import list_valid_skills

pytestmark = pytest.mark.unit


def test_list_valid_skills_empty_directory(tmp_path: Path) -> None:
    """When skills directory is empty, returns empty list."""
    skills = tmp_path / "skills"
    skills.mkdir()
    assert list_valid_skills(skills) == []


def test_list_valid_skills_valid_and_invalid_names(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Valid kebab-case names are returned; invalid names are skipped and logged; files ignored."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Valid
    for name in ["valid-skill", "another-valid", "a", "a1", "skill-123", "a-b-c-d"]:
        (skills / name).mkdir()

    # Invalid
    for name in [
        "Invalid_Name",
        "CamelCase",
        "123-starts-with-number",
        "has spaces",
        "-starts-with-dash",
        "ends-with-dash-",
        "double--dash",
    ]:
        (skills / name).mkdir()

    # File (should be ignored)
    (skills / "README.md").write_text("readme", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = list_valid_skills(skills)

    result_names = sorted([p.name for p in result])
    assert result_names == ["a", "a-b-c-d", "a1", "another-valid", "skill-123", "valid-skill"]

    # All invalid names should appear in warnings
    for invalid in ["Invalid_Name", "CamelCase", "123-starts-with-number"]:
        assert invalid in caplog.text


def test_list_valid_skills_edge_cases(tmp_path: Path) -> None:
    """Regex pattern ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ edge cases."""
    skills = tmp_path / "skills"
    skills.mkdir()

    for name in ["a", "abc", "a123", "ab-cd", "a-b-c", "skill-123-test"]:
        (skills / name).mkdir()
    for name in ["A", "1", "-a", "a-", "a--b"]:
        (skills / name).mkdir()

    result = list_valid_skills(skills)
    result_names = sorted([p.name for p in result])
    assert result_names == ["a", "a-b-c", "a123", "ab-cd", "abc", "skill-123-test"]
