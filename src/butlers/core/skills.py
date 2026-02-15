"""Butler skills infrastructure — helpers for CLAUDE.md, AGENTS.md, and skills directories.

Provides utility functions the CC spawner uses to read system prompts,
manage runtime agent notes, and locate skill directories within a butler's
config directory.

Butler config directory layout::

    butler-name/
    ├── CLAUDE.md       # Butler personality/instructions (system prompt)
    ├── AGENTS.md       # Runtime agent notes (read/write by CC instances)
    ├── skills/         # Skills available to CC instances
    │   └── <name>/
    │       └── SKILL.md
    └── butler.toml     # Identity, schedule, modules config
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_TEMPLATE = "You are the {butler_name} butler."

# Kebab-case validation pattern: starts with lowercase letter, followed by lowercase letters/digits,
# optionally followed by groups of hyphen + lowercase letters/digits
_KEBAB_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Include directive: <!-- @include shared/NOTIFY.md -->
_INCLUDE_PATTERN = re.compile(r"^\s*<!--\s*@include\s+([\w/._-]+\.md)\s*-->\s*$")


# ---------------------------------------------------------------------------
# 9.1 — CLAUDE.md and skills directory for CC spawner
# ---------------------------------------------------------------------------


def _resolve_includes(content: str, roster_dir: Path) -> str:
    """Replace ``<!-- @include path/to/file.md -->`` directives with file contents.

    Paths are resolved relative to *roster_dir* (the parent of a butler's
    config directory).  ``..`` segments are rejected (logged as a warning and
    the directive is preserved as-is).  Includes are **not** recursive — any
    directives inside included files are left untouched.
    """
    lines = content.split("\n")
    out: list[str] = []
    for line in lines:
        m = _INCLUDE_PATTERN.match(line)
        if m is None:
            out.append(line)
            continue
        rel_path = m.group(1)
        if ".." in rel_path.split("/"):
            logger.warning("Include path contains '..', skipping: %s", rel_path)
            out.append(line)
            continue
        target = roster_dir / rel_path
        if not target.is_file():
            logger.warning("Include file not found, preserving directive: %s", target)
            out.append(line)
            continue
        included = target.read_text(encoding="utf-8").rstrip("\n")
        out.append(included)
    return "\n".join(out)


def _append_shared_skills(content: str, roster_dir: Path) -> str:
    """Append ``BUTLER_SKILLS.md`` from the shared directory if it exists.

    The file is expected at ``roster_dir / "shared" / "BUTLER_SKILLS.md"``.
    When present, its contents are appended to *content* separated by two
    newlines.  Otherwise *content* is returned unchanged.
    """
    butler_skills = roster_dir / "shared" / "BUTLER_SKILLS.md"
    if butler_skills.is_file():
        skills_content = butler_skills.read_text(encoding="utf-8").rstrip("\n")
        if skills_content:
            return content + "\n\n" + skills_content
    return content


def read_system_prompt(config_dir: Path, butler_name: str) -> str:
    """Read the system prompt from *config_dir*/CLAUDE.md.

    Returns the file content if present and non-empty.  Otherwise returns a
    sensible default incorporating the butler's name.

    Any ``<!-- @include ... -->`` directives are resolved relative to the
    roster directory (``config_dir.parent``).  After include resolution,
    ``BUTLER_SKILLS.md`` from the shared directory is appended if present.
    """
    claude_md = config_dir / "CLAUDE.md"
    if claude_md.is_file():
        content = claude_md.read_text(encoding="utf-8").strip()
        if content:
            roster_dir = config_dir.parent
            content = _resolve_includes(content, roster_dir)
            return _append_shared_skills(content, roster_dir)
    default = _DEFAULT_PROMPT_TEMPLATE.format(butler_name=butler_name)
    logger.debug("CLAUDE.md missing or empty in %s — using default prompt", config_dir)
    return default


def get_skills_dir(config_dir: Path) -> Path | None:
    """Return the path to *config_dir*/skills/ if it exists, else ``None``."""
    skills = config_dir / "skills"
    if skills.is_dir():
        return skills
    return None


def is_valid_skill_name(name: str) -> bool:
    """Check if a skill directory name is valid kebab-case.

    Valid pattern: ^[a-z][a-z0-9]*(-[a-z0-9]+)*$
    - Must start with a lowercase letter
    - Can contain lowercase letters and digits
    - Can contain hyphens to separate segments
    - Each segment after a hyphen must have at least one character
    - Cannot start or end with a hyphen
    - Cannot have consecutive hyphens
    """
    return _KEBAB_CASE_PATTERN.match(name) is not None


def list_valid_skills(skills_dir: Path) -> list[Path]:
    """List all valid skill directories in the given skills directory.

    Returns only directories with valid kebab-case names.
    Invalid directories are logged as warnings and skipped.

    Parameters
    ----------
    skills_dir:
        Path to the skills directory to scan.

    Returns
    -------
    list[Path]
        List of paths to valid skill directories, sorted by name.
    """
    if not skills_dir.is_dir():
        logger.warning("Skills directory does not exist: %s", skills_dir)
        return []

    valid_skills: list[Path] = []

    for item in skills_dir.iterdir():
        # Only process directories, skip files
        if not item.is_dir():
            continue

        skill_name = item.name

        if is_valid_skill_name(skill_name):
            valid_skills.append(item)
        else:
            logger.warning(
                "Skipping skill directory with invalid name (must be kebab-case): %s", skill_name
            )

    return sorted(valid_skills, key=lambda p: p.name)


# ---------------------------------------------------------------------------
# 9.2 — AGENTS.md read / write access
# ---------------------------------------------------------------------------


def read_agents_md(config_dir: Path) -> str:
    """Read AGENTS.md from *config_dir*.  Returns empty string if the file is absent."""
    agents_md = config_dir / "AGENTS.md"
    if agents_md.is_file():
        return agents_md.read_text(encoding="utf-8")
    return ""


def write_agents_md(config_dir: Path, content: str) -> None:
    """Write *content* to AGENTS.md in *config_dir*, creating the file if needed."""
    agents_md = config_dir / "AGENTS.md"
    agents_md.write_text(content, encoding="utf-8")


def append_agents_md(config_dir: Path, content: str) -> None:
    """Append *content* to AGENTS.md in *config_dir*, creating the file if needed."""
    agents_md = config_dir / "AGENTS.md"
    existing = ""
    if agents_md.is_file():
        existing = agents_md.read_text(encoding="utf-8")
    agents_md.write_text(existing + content, encoding="utf-8")
