"""Butler skills infrastructure — helpers for CLAUDE.md, AGENTS.md, and skills directories.

Provides utility functions the LLM CLI spawner uses to read system prompts,
manage runtime agent notes, and locate skill directories within a butler's
config directory.

Butler config directory layout::

    butler-name/
    ├── CLAUDE.md       # Butler personality/instructions (system prompt)
    ├── AGENTS.md       # Runtime agent notes (read/write by runtime instances)
    ├── .agents/skills/ # Skills available to runtime instances (Codex discovery)
    │   └── <name>/
    │       └── SKILL.md
    ├── .claude -> .agents  # Claude Code compatibility symlink
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

# Include directives:
# - HTML form, resolved relative to the roster directory:
#   <!-- @include shared/NOTIFY.md -->
# - Codex/Claude-style bare file references, resolved relative to the file
#   that contains the directive:
#   @AGENTS.md
#   @../shared/AGENTS.md
_INCLUDE_PATTERN = re.compile(r"^\s*<!--\s*@include\s+([\w/._-]+\.md)\s*-->\s*$")
_BARE_INCLUDE_PATTERN = re.compile(r"^\s*@([\w/._-]+\.md)\s*$")


# ---------------------------------------------------------------------------
# 9.1 — CLAUDE.md and skills directory for LLM CLI spawner
# ---------------------------------------------------------------------------


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Return True when *child* resolves under *parent*."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_includes(
    content: str,
    roster_dir: Path,
    base_dir: Path | None = None,
    _seen: set[Path] | None = None,
) -> str:
    """Replace supported include directives with file contents.

    HTML ``@include`` paths are resolved relative to *roster_dir* for backward
    compatibility. Bare ``@file.md`` paths are resolved relative to *base_dir*
    (the butler config directory) so roster files can delegate ``CLAUDE.md`` to
    ``AGENTS.md``. HTML includes remain non-recursive for backward
    compatibility; bare includes recurse with cycle protection so existing
    ``CLAUDE.md -> AGENTS.md -> ../shared/AGENTS.md`` chains expand fully.
    """
    roster_root = roster_dir.resolve()
    bare_base_dir = (base_dir or roster_dir).resolve()
    seen = set(_seen or ())
    lines = content.split("\n")
    out: list[str] = []
    for line in lines:
        m = _INCLUDE_PATTERN.match(line)
        if m is not None:
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
            continue

        m = _BARE_INCLUDE_PATTERN.match(line)
        if m is None:
            out.append(line)
            continue

        rel_path = m.group(1)
        target = (bare_base_dir / rel_path).resolve()
        if not _is_relative_to(target, roster_root):
            logger.warning("Bare include path escapes roster, skipping: %s", rel_path)
            out.append(line)
            continue
        if target in seen:
            logger.warning("Bare include cycle detected, skipping: %s", target)
            out.append(line)
            continue
        if not target.is_file():
            logger.warning("Include file not found, preserving directive: %s", target)
            out.append(line)
            continue
        included = target.read_text(encoding="utf-8").rstrip("\n")
        included = _resolve_includes(
            included,
            roster_dir,
            base_dir=target.parent,
            _seen={*seen, target},
        )
        out.append(included)
    return "\n".join(out)


def _append_shared_markdown(content: str, roster_dir: Path, filename: str) -> str:
    """Append ``shared/<filename>`` contents if the file exists and is non-empty."""
    shared_file = roster_dir / "shared" / filename
    if not shared_file.is_file():
        return content

    shared_content = shared_file.read_text(encoding="utf-8").rstrip("\n")
    if not shared_content:
        return content

    return content + "\n\n" + shared_content


def _append_shared_files(content: str, roster_dir: Path) -> str:
    """Append shared prompt snippets after include resolution.

    Order is intentional and stable:
    1. ``BUTLER_SKILLS.md``
    2. ``MCP_LOGGING.md``
    """
    content = _append_shared_markdown(content, roster_dir, "BUTLER_SKILLS.md")
    return _append_shared_markdown(content, roster_dir, "MCP_LOGGING.md")


def process_system_prompt_base(base_content: str, config_dir: Path) -> str:
    """Resolve includes and append shared snippets for a raw base prompt.

    *base_content* is the raw system-prompt body (either the on-disk
    ``CLAUDE.md`` content or a DB-stored override). ``<!-- @include ... -->``
    and bare ``@file.md`` directives are resolved relative to the roster
    directory (``config_dir.parent``) / config directory, then shared snippets
    (``BUTLER_SKILLS.md`` and ``MCP_LOGGING.md``) are appended if present.

    This keeps the include/shared-file processing identical regardless of
    whether the base prompt comes from disk or the database.
    """
    roster_dir = config_dir.parent
    content = _resolve_includes(base_content, roster_dir, base_dir=config_dir)
    return _append_shared_files(content, roster_dir)


def read_system_prompt(
    config_dir: Path,
    butler_name: str,
    db_override: str | None = None,
) -> str:
    """Resolve the system prompt for a butler.

    Resolution order (DB is the live override, disk is the seed/default):

    1. If *db_override* is a non-empty string, it is treated as the raw base
       prompt (the HEAD of ``public.system_prompt_history``). This lets the
       dashboard's prompt editor take effect on the next spawned session.
    2. Otherwise, the on-disk *config_dir*/``CLAUDE.md`` content is used when
       present and non-empty.
    3. Otherwise, a sensible default incorporating the butler's name is used.

    In cases 1 and 2 the base content is passed through
    :func:`process_system_prompt_base` so ``@include`` directives and shared
    snippets (``BUTLER_SKILLS.md``, ``MCP_LOGGING.md``) are applied uniformly.
    """
    if db_override is not None:
        base = db_override.strip()
        if base:
            return process_system_prompt_base(base, config_dir)

    claude_md = config_dir / "CLAUDE.md"
    if claude_md.is_file():
        content = claude_md.read_text(encoding="utf-8").strip()
        if content:
            return process_system_prompt_base(content, config_dir)
    default = _DEFAULT_PROMPT_TEMPLATE.format(butler_name=butler_name)
    logger.debug("CLAUDE.md missing or empty in %s — using default prompt", config_dir)
    return default


def get_skills_dir(config_dir: Path) -> Path | None:
    """Return the path to *config_dir*/.agents/skills/ if it exists, else ``None``."""
    skills = config_dir / ".agents" / "skills"
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
