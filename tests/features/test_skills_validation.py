"""Tests for skill directory name validation (kebab-case)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from butlers.core.skills import list_valid_skills

pytestmark = pytest.mark.unit


def test_list_valid_skills_empty_directory(tmp_path: Path) -> None:
    """When skills directory is empty, returns empty list."""
    skills = tmp_path / "skills"
    skills.mkdir()
    assert list_valid_skills(skills) == []


def test_list_valid_skills_kebab_case_pattern(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Valid kebab-case dirs are returned; invalid names skipped+logged; files ignored.

    Exercises the ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ pattern across its edge cases.
    """
    skills = tmp_path / "skills"
    skills.mkdir()

    # Valid (incl. single-char + numeric-suffix edge cases)
    valid = ["valid-skill", "another-valid", "a", "a1", "skill-123", "a-b-c-d", "abc", "ab-cd"]
    for name in valid:
        (skills / name).mkdir()

    # Invalid (incl. regex edge cases: leading number/dash, trailing dash, double dash)
    invalid = [
        "Invalid_Name",
        "CamelCase",
        "123-starts-with-number",
        "has spaces",
        "-starts-with-dash",
        "ends-with-dash-",
        "double--dash",
        "A",
        "1",
    ]
    for name in invalid:
        (skills / name).mkdir()

    # File (should be ignored)
    (skills / "README.md").write_text("readme", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = list_valid_skills(skills)

    result_names = sorted(p.name for p in result)
    assert result_names == sorted(valid)

    # Invalid names should appear in warnings.
    for bad in ["Invalid_Name", "CamelCase", "123-starts-with-number"]:
        assert bad in caplog.text


def test_repo_skill_files_use_yaml_frontmatter() -> None:
    """Repo skill files must use Codex-compatible YAML frontmatter."""
    repo_root = Path(__file__).resolve().parents[2]
    skill_files = sorted(repo_root.glob("roster/**/SKILL.md"))

    missing_frontmatter: list[str] = []
    missing_name: list[str] = []
    mismatched_name: list[str] = []
    missing_description: list[str] = []

    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        rel_path = skill_file.relative_to(repo_root).as_posix()

        if len(lines) < 3 or lines[0].strip() != "---":
            missing_frontmatter.append(rel_path)
            continue

        try:
            closing_index = lines.index("---", 1)
        except ValueError:
            missing_frontmatter.append(rel_path)
            continue

        frontmatter = lines[1:closing_index]
        name_line = next(
            (line for line in frontmatter if line and line.lstrip().startswith("name:")),
            None,
        )
        if name_line is None:
            missing_name.append(rel_path)
            continue

        parts = name_line.split(":", 1)
        if len(parts) < 2:
            missing_name.append(rel_path)
            continue

        skill_name = parts[1].strip().strip("'\"")
        if not skill_name:
            missing_name.append(rel_path)
            continue

        expected_name = skill_file.parent.name
        if skill_name != expected_name:
            mismatched_name.append(f"{rel_path} -> {skill_name!r} != {expected_name!r}")

        # Every Codex-discoverable skill must carry a non-empty description.
        desc_line = next(
            (line for line in frontmatter if line and line.lstrip().startswith("description:")),
            None,
        )
        desc_value = desc_line.split(":", 1)[1].strip().strip("'\"") if desc_line else ""
        if not desc_value:
            missing_description.append(rel_path)

    assert missing_frontmatter == []
    assert missing_name == []
    assert mismatched_name == []
    assert missing_description == []


# RFC 0038 D18 grants engineering executors a shell in an isolated worktree.
_ENGINEERING_ONLY_SKILLS = frozenset({"roster/qa/.agents/skills/investigation-notes/SKILL.md"})
_SKILL_SHELL_OR_HTTP = re.compile(
    r"\b(?:import_csv|urllib|subprocess)\b|\brequests\.|\bcurl\s+|"
    r"\b(?:write|run|execute)\s+(?:a|the)\s+script\b|"
    r"^\s*(?:\$\s*)?(?:python(?:3(?:\.\d+)?)?|bash|sh|mkdir)\s+|"
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD)\s+(?:https?://|/api/)",
    re.IGNORECASE | re.MULTILINE,
)


def _skill_execution_instructions(text: str) -> list[str]:
    """Scan shipped instructions while allowing Markdown link destinations."""
    # Only URLs in Markdown destinations and autolinks are plain references.
    # Keep any visible labels, prose and fenced examples under the same scan.
    text = re.sub(r"(?<=\]\()https?://[^\s)]+(?=\))|<https?://[^\s>]+>", "", text)
    return [match.group().strip() for match in _SKILL_SHELL_OR_HTTP.finditer(text)]


def test_roster_skills_use_capabilities_without_shell_or_http() -> None:
    """Owner-facing skill instructions stay within the RFC 0038 D2/D3 surface."""
    for instruction in (
        "import_csv.py",
        "urllib.request",
        "requests.post(url)",
        "curl url",
        "subprocess.run(cmd)",
        "python import.py",
        "$ python3 -c code",
        "Write a script",
        "Run the script",
        "POST /api/finance/transactions/bulk",
    ):
        assert _skill_execution_instructions(instruction), instruction
    assert not _skill_execution_instructions(
        "[reference](https://example.test/urllib)\n"
        "```python\nawait import_transactions(storage_ref=ref, dry_run=True)\n```"
    )

    repo_root = Path(__file__).resolve().parents[2]
    skills = {
        skill.relative_to(repo_root).as_posix(): skill
        for skill in repo_root.glob("roster/**/SKILL.md")
    }
    assert skills and _ENGINEERING_ONLY_SKILLS.issubset(skills)
    violations = {}
    for location, skill in sorted(skills.items()):
        if location in _ENGINEERING_ONLY_SKILLS:
            continue
        if instructions := _skill_execution_instructions(skill.read_text(encoding="utf-8")):
            violations[location] = instructions
    assert not violations, violations
