"""Tests for butler skills infrastructure (CLAUDE.md, AGENTS.md, skills dir)."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.core.skills import (
    append_agents_md,
    get_skills_dir,
    read_agents_md,
    read_system_prompt,
    write_agents_md,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# 9.1 — read_system_prompt
# ---------------------------------------------------------------------------


def test_read_system_prompt_returns_file_content(tmp_path: Path) -> None:
    """When CLAUDE.md is present its content is returned as the system prompt."""
    (tmp_path / "CLAUDE.md").write_text("You are the mail butler.", encoding="utf-8")
    assert read_system_prompt(tmp_path, "mail") == "You are the mail butler."


def test_read_system_prompt_missing_file_returns_default(tmp_path: Path) -> None:
    """When CLAUDE.md does not exist, a default prompt containing the butler name is returned."""
    result = read_system_prompt(tmp_path, "jarvis")
    assert result == "You are the jarvis butler."


def test_read_system_prompt_empty_file_returns_default(tmp_path: Path) -> None:
    """When CLAUDE.md exists but is empty (or whitespace-only), the default prompt is returned."""
    (tmp_path / "CLAUDE.md").write_text("   \n  ", encoding="utf-8")
    result = read_system_prompt(tmp_path, "alfred")
    assert result == "You are the alfred butler."


# ---------------------------------------------------------------------------
# 9.1 — @include directive resolution
# ---------------------------------------------------------------------------


def _setup_roster(tmp_path: Path, butler: str = "test-butler") -> Path:
    """Create a roster/<butler>/ layout and return the butler config dir."""
    config_dir = tmp_path / butler
    config_dir.mkdir()
    return config_dir


def test_read_system_prompt_resolves_includes(tmp_path: Path) -> None:
    """A <!-- @include shared/FOO.md --> directive is replaced with file contents."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "NOTIFY.md").write_text("Notify instructions here.", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text(
        "# Butler\n<!-- @include shared/NOTIFY.md -->\nDone.", encoding="utf-8"
    )
    result = read_system_prompt(config_dir, "test")
    assert "Notify instructions here." in result
    assert "<!-- @include" not in result
    assert result.startswith("# Butler\n")
    assert result.endswith("\nDone.")


def test_read_system_prompt_missing_include_preserves_directive(tmp_path: Path) -> None:
    """When the included file does not exist, the directive is preserved as-is."""
    config_dir = _setup_roster(tmp_path)
    directive = "<!-- @include shared/MISSING.md -->"
    (config_dir / "CLAUDE.md").write_text(f"# Butler\n{directive}\nEnd.", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert directive in result


def test_read_system_prompt_no_recursive_includes(tmp_path: Path) -> None:
    """Directives inside included files are NOT resolved (no recursion)."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "OUTER.md").write_text(
        "Outer content\n<!-- @include shared/INNER.md -->", encoding="utf-8"
    )
    (shared / "INNER.md").write_text("Inner content", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("<!-- @include shared/OUTER.md -->", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert "Outer content" in result
    # The nested directive should be present verbatim, NOT resolved
    assert "<!-- @include shared/INNER.md -->" in result
    assert "Inner content" not in result


def test_read_system_prompt_multiple_includes(tmp_path: Path) -> None:
    """Multiple include directives in one file are all resolved."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "A.md").write_text("AAA", encoding="utf-8")
    (shared / "B.md").write_text("BBB", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text(
        "Start\n<!-- @include shared/A.md -->\nMiddle\n<!-- @include shared/B.md -->\nEnd",
        encoding="utf-8",
    )
    result = read_system_prompt(config_dir, "test")
    assert "AAA" in result
    assert "BBB" in result
    assert "<!-- @include" not in result
    assert result == "Start\nAAA\nMiddle\nBBB\nEnd"


def test_include_rejects_path_traversal(tmp_path: Path) -> None:
    """Include paths with '..' segments are rejected and the directive is preserved."""
    config_dir = _setup_roster(tmp_path)
    # Create the file that a traversal would reach
    (tmp_path / "secret.md").write_text("SECRET", encoding="utf-8")
    directive = "<!-- @include ../secret.md -->"
    (config_dir / "CLAUDE.md").write_text(directive, encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert directive in result
    assert "SECRET" not in result


# ---------------------------------------------------------------------------
# 9.1 — BUTLER_SKILLS.md auto-append
# ---------------------------------------------------------------------------


def test_read_system_prompt_appends_butler_skills(tmp_path: Path) -> None:
    """BUTLER_SKILLS.md from shared/ is auto-appended to the system prompt."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "BUTLER_SKILLS.md").write_text("## Shared Skills\n- skill-a", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("# Butler prompt", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert result == "# Butler prompt\n\n## Shared Skills\n- skill-a"


def test_read_system_prompt_no_butler_skills_file(tmp_path: Path) -> None:
    """When BUTLER_SKILLS.md is absent, the prompt is unchanged."""
    config_dir = _setup_roster(tmp_path)
    (config_dir / "CLAUDE.md").write_text("# Butler prompt", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert result == "# Butler prompt"


def test_butler_skills_appended_after_includes(tmp_path: Path) -> None:
    """Includes are resolved first, then BUTLER_SKILLS.md is appended."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "NOTIFY.md").write_text("Notify content", encoding="utf-8")
    (shared / "BUTLER_SKILLS.md").write_text("## Skills", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text(
        "# Butler\n<!-- @include shared/NOTIFY.md -->\nEnd.", encoding="utf-8"
    )
    result = read_system_prompt(config_dir, "test")
    # Includes resolved
    assert "Notify content" in result
    assert "<!-- @include" not in result
    # Skills appended at the end
    assert result.endswith("\n\n## Skills")


def test_read_system_prompt_default_no_butler_skills(tmp_path: Path) -> None:
    """Default prompt (no CLAUDE.md) does not get BUTLER_SKILLS.md appended."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "BUTLER_SKILLS.md").write_text("## Skills", encoding="utf-8")
    # No CLAUDE.md — should get default prompt without skills
    result = read_system_prompt(config_dir, "test")
    assert result == "You are the test butler."
    assert "Skills" not in result


def test_read_system_prompt_appends_mcp_logging(tmp_path: Path) -> None:
    """MCP_LOGGING.md from shared/ is auto-appended to the system prompt."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "MCP_LOGGING.md").write_text(
        "# MCP Logging Requirements\n1. List tools.\n2. Report errors.",
        encoding="utf-8",
    )
    (config_dir / "CLAUDE.md").write_text("# Butler prompt", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert (
        result == "# Butler prompt\n\n# MCP Logging Requirements\n1. List tools.\n2. Report errors."
    )


def test_read_system_prompt_appends_skills_then_mcp_logging(tmp_path: Path) -> None:
    """Shared append order remains stable: skills first, then MCP logging."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "BUTLER_SKILLS.md").write_text("## Skills", encoding="utf-8")
    (shared / "MCP_LOGGING.md").write_text("# MCP Logging", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("# Butler prompt", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert result == "# Butler prompt\n\n## Skills\n\n# MCP Logging"


def test_read_system_prompt_default_no_mcp_logging(tmp_path: Path) -> None:
    """Default prompt (no CLAUDE.md) does not get MCP_LOGGING.md appended."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "MCP_LOGGING.md").write_text("# MCP Logging Requirements", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert result == "You are the test butler."
    assert "MCP Logging Requirements" not in result


# ---------------------------------------------------------------------------
# 9.1 — get_skills_dir
# ---------------------------------------------------------------------------


def test_get_skills_dir_exists(tmp_path: Path) -> None:
    """When skills/ exists, the path is returned."""
    skills = tmp_path / "skills"
    skills.mkdir()
    assert get_skills_dir(tmp_path) == skills


def test_get_skills_dir_missing(tmp_path: Path) -> None:
    """When skills/ does not exist, None is returned."""
    assert get_skills_dir(tmp_path) is None


def test_skills_directory_structure(tmp_path: Path) -> None:
    """Validates the expected skills/<name>/SKILL.md layout."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "email-send").mkdir()
    (skills / "email-send" / "SKILL.md").write_text("# Email Send Skill", encoding="utf-8")
    (skills / "calendar-check").mkdir()
    (skills / "calendar-check" / "SKILL.md").write_text("# Calendar Check", encoding="utf-8")

    result = get_skills_dir(tmp_path)
    assert result is not None

    # Each subdirectory should contain a SKILL.md
    skill_dirs = sorted(p.name for p in result.iterdir() if p.is_dir())
    assert skill_dirs == ["calendar-check", "email-send"]
    for skill_name in skill_dirs:
        skill_md = result / skill_name / "SKILL.md"
        assert skill_md.is_file(), f"SKILL.md missing for skill {skill_name}"


# ---------------------------------------------------------------------------
# 9.2 — AGENTS.md read / write / append
# ---------------------------------------------------------------------------


def test_read_agents_md_present(tmp_path: Path) -> None:
    """When AGENTS.md exists, its content is returned."""
    (tmp_path / "AGENTS.md").write_text("# Agent Notes\nfoo", encoding="utf-8")
    assert read_agents_md(tmp_path) == "# Agent Notes\nfoo"


def test_read_agents_md_missing(tmp_path: Path) -> None:
    """When AGENTS.md is missing, an empty string is returned."""
    assert read_agents_md(tmp_path) == ""


def test_write_agents_md_creates_file(tmp_path: Path) -> None:
    """write_agents_md creates the file and persists content."""
    write_agents_md(tmp_path, "hello world")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "hello world"


def test_write_agents_md_overwrites(tmp_path: Path) -> None:
    """write_agents_md replaces existing content entirely."""
    (tmp_path / "AGENTS.md").write_text("old stuff", encoding="utf-8")
    write_agents_md(tmp_path, "new stuff")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "new stuff"


def test_append_agents_md_existing(tmp_path: Path) -> None:
    """append_agents_md adds to existing content without replacing it."""
    (tmp_path / "AGENTS.md").write_text("line1\n", encoding="utf-8")
    append_agents_md(tmp_path, "line2\n")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "line1\nline2\n"


def test_append_agents_md_creates_file(tmp_path: Path) -> None:
    """append_agents_md creates the file when it does not exist."""
    append_agents_md(tmp_path, "first entry\n")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "first entry\n"
