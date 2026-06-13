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


def test_read_system_prompt_basic(tmp_path: Path) -> None:
    """Present file returns content; missing/empty file returns default prompt."""
    (tmp_path / "CLAUDE.md").write_text("You are the mail butler.", encoding="utf-8")
    assert read_system_prompt(tmp_path, "mail") == "You are the mail butler."

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert read_system_prompt(empty_dir, "jarvis") == "You are the jarvis butler."

    (tmp_path / "ws_dir").mkdir()
    (tmp_path / "ws_dir" / "CLAUDE.md").write_text("   \n  ", encoding="utf-8")
    assert read_system_prompt(tmp_path / "ws_dir", "alfred") == "You are the alfred butler."


# ---------------------------------------------------------------------------
# 9.1 — @include directive resolution
# ---------------------------------------------------------------------------


def _setup_roster(tmp_path: Path, butler: str = "test-butler") -> Path:
    """Create a roster/<butler>/ layout and return the butler config dir."""
    config_dir = tmp_path / butler
    config_dir.mkdir()
    return config_dir


def test_read_system_prompt_include_resolution(tmp_path: Path) -> None:
    """Includes are resolved; missing include preserved; no recursive resolution."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()

    # Resolves single include
    (shared / "NOTIFY.md").write_text("Notify instructions here.", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text(
        "# Butler\n<!-- @include shared/NOTIFY.md -->\nDone.", encoding="utf-8"
    )
    result = read_system_prompt(config_dir, "test")
    assert "Notify instructions here." in result
    assert "<!-- @include" not in result

    # Missing include preserved as-is
    directive = "<!-- @include shared/MISSING.md -->"
    (config_dir / "CLAUDE.md").write_text(f"# Butler\n{directive}\nEnd.", encoding="utf-8")
    result2 = read_system_prompt(config_dir, "test")
    assert directive in result2

    # No recursive resolution
    (shared / "OUTER.md").write_text(
        "Outer content\n<!-- @include shared/INNER.md -->", encoding="utf-8"
    )
    (shared / "INNER.md").write_text("Inner content", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("<!-- @include shared/OUTER.md -->", encoding="utf-8")
    result3 = read_system_prompt(config_dir, "test")
    assert "Outer content" in result3
    assert "<!-- @include shared/INNER.md -->" in result3
    assert "Inner content" not in result3


def test_read_system_prompt_bare_include_resolution(tmp_path: Path) -> None:
    """Bare @file.md references resolve relative to the butler config directory."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()

    (config_dir / "AGENTS.md").write_text("# Agent instructions\nUse MCP tools.", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("@AGENTS.md", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert "# Agent instructions" in result
    assert "Use MCP tools." in result
    assert "@AGENTS.md" not in result

    (shared / "AGENTS.md").write_text("# Shared instructions", encoding="utf-8")
    (config_dir / "AGENTS.md").write_text("@../shared/AGENTS.md\n# Agent instructions")
    (config_dir / "CLAUDE.md").write_text("@AGENTS.md", encoding="utf-8")
    result_nested = read_system_prompt(config_dir, "test")
    assert "# Shared instructions" in result_nested
    assert "@../shared/AGENTS.md" not in result_nested

    (config_dir / "CLAUDE.md").write_text("@../shared/AGENTS.md", encoding="utf-8")
    result2 = read_system_prompt(config_dir, "test")
    assert result2 == "# Shared instructions"


def test_read_system_prompt_bare_include_rejects_roster_escape(tmp_path: Path) -> None:
    """Bare include references may move within roster but not outside it."""
    config_dir = _setup_roster(tmp_path)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("SECRET", encoding="utf-8")

    directive = "@../../outside.md"
    (config_dir / "CLAUDE.md").write_text(directive, encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert directive in result
    assert "SECRET" not in result


def test_read_system_prompt_multiple_includes_and_traversal(tmp_path: Path) -> None:
    """Multiple includes resolved; path traversal rejected."""
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
    assert result == "Start\nAAA\nMiddle\nBBB\nEnd"

    # Path traversal rejected
    (tmp_path / "secret.md").write_text("SECRET", encoding="utf-8")
    directive = "<!-- @include ../secret.md -->"
    (config_dir / "CLAUDE.md").write_text(directive, encoding="utf-8")
    result2 = read_system_prompt(config_dir, "test")
    assert directive in result2
    assert "SECRET" not in result2


def test_read_system_prompt_butler_skills_appended(tmp_path: Path) -> None:
    """BUTLER_SKILLS.md and MCP_LOGGING.md are appended in order; default prompt skips them."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()

    # Skills append
    (shared / "BUTLER_SKILLS.md").write_text("## Shared Skills\n- skill-a", encoding="utf-8")
    (config_dir / "CLAUDE.md").write_text("# Butler prompt", encoding="utf-8")
    result = read_system_prompt(config_dir, "test")
    assert result == "# Butler prompt\n\n## Shared Skills\n- skill-a"

    # Default prompt (no CLAUDE.md) does not append skills
    empty_dir = tmp_path / "no_prompt_dir"
    empty_dir.mkdir()
    (tmp_path / "shared2").mkdir()
    (tmp_path / "shared2" / "BUTLER_SKILLS.md").write_text("## Skills", encoding="utf-8")
    result2 = read_system_prompt(empty_dir, "test")
    assert result2 == "You are the test butler."

    # Skills + MCP_LOGGING appended in stable order
    (shared / "MCP_LOGGING.md").write_text("# MCP Logging", encoding="utf-8")
    result3 = read_system_prompt(config_dir, "test")
    assert result3 == "# Butler prompt\n\n## Shared Skills\n- skill-a\n\n# MCP Logging"


# ---------------------------------------------------------------------------
# 9.1 — DB override (live prompt edits, bu-dr03f.1)
# ---------------------------------------------------------------------------


def test_read_system_prompt_db_override_takes_precedence(tmp_path: Path) -> None:
    """A non-empty db_override is used over on-disk CLAUDE.md."""
    config_dir = _setup_roster(tmp_path)
    (config_dir / "CLAUDE.md").write_text("# On-disk seed prompt", encoding="utf-8")

    result = read_system_prompt(config_dir, "test", db_override="# Edited live prompt")
    assert result == "# Edited live prompt"
    assert "On-disk seed prompt" not in result


def test_read_system_prompt_db_override_falls_back_when_absent(tmp_path: Path) -> None:
    """No / empty override falls back to the on-disk CLAUDE.md seed."""
    config_dir = _setup_roster(tmp_path)
    (config_dir / "CLAUDE.md").write_text("# On-disk seed prompt", encoding="utf-8")

    # None override -> disk
    assert read_system_prompt(config_dir, "test", db_override=None) == "# On-disk seed prompt"
    # Blank/whitespace override -> disk (treated as no override)
    assert read_system_prompt(config_dir, "test", db_override="   \n ") == "# On-disk seed prompt"


def test_read_system_prompt_db_override_resolves_includes_and_shared(tmp_path: Path) -> None:
    """The DB override is processed through include + shared-file resolution too."""
    config_dir = _setup_roster(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "NOTIFY.md").write_text("Notify body.", encoding="utf-8")
    (shared / "BUTLER_SKILLS.md").write_text("## Skills\n- a", encoding="utf-8")

    override = "# Live\n<!-- @include shared/NOTIFY.md -->\nEnd."
    result = read_system_prompt(config_dir, "test", db_override=override)
    assert "Notify body." in result
    assert "<!-- @include" not in result
    assert result.endswith("## Skills\n- a")


# ---------------------------------------------------------------------------
# 9.1 — get_skills_dir
# ---------------------------------------------------------------------------


def test_get_skills_dir(tmp_path: Path) -> None:
    """Returns path when .agents/skills/ exists; None when absent; correct layout."""
    skills = tmp_path / ".agents" / "skills"
    skills.mkdir(parents=True)
    assert get_skills_dir(tmp_path) == skills

    assert get_skills_dir(tmp_path / "no_skills") is None

    # Structure validation
    (skills / "email-send").mkdir()
    (skills / "email-send" / "SKILL.md").write_text("# Email Send Skill", encoding="utf-8")
    (skills / "calendar-check").mkdir()
    (skills / "calendar-check" / "SKILL.md").write_text("# Calendar Check", encoding="utf-8")
    result = get_skills_dir(tmp_path)
    assert result is not None
    skill_dirs = sorted(p.name for p in result.iterdir() if p.is_dir())
    assert skill_dirs == ["calendar-check", "email-send"]
    for skill_name in skill_dirs:
        assert (result / skill_name / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# 9.2 — AGENTS.md read / write / append
# ---------------------------------------------------------------------------


def test_agents_md_read_write_append(tmp_path: Path) -> None:
    """read returns content or empty; write creates/overwrites; append extends."""
    assert read_agents_md(tmp_path) == ""

    (tmp_path / "AGENTS.md").write_text("# Agent Notes\nfoo", encoding="utf-8")
    assert read_agents_md(tmp_path) == "# Agent Notes\nfoo"

    write_agents_md(tmp_path, "new stuff")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "new stuff"

    append_agents_md(tmp_path, " and more\n")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "new stuff and more\n"

    tmp2 = tmp_path / "sub"
    tmp2.mkdir()
    append_agents_md(tmp2, "first entry\n")
    assert (tmp2 / "AGENTS.md").read_text(encoding="utf-8") == "first entry\n"
