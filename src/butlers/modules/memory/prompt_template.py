"""Consolidation prompt builder for the Memory Butler.

Reads the SKILL.md template and appends contextual sections (episodes,
existing facts, existing rules) to produce a complete prompt string for
a CC consolidation session.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_MD_PATH = Path(__file__).resolve().parent / "skills" / "consolidate" / "SKILL.md"


def _format_episodes(episodes: list[dict]) -> str:
    """Format episode dicts into a readable markdown section with XML delimitation.

    Episode content is wrapped in XML tags to prevent prompt injection attacks.
    The LLM is explicitly instructed to treat episode content as data only.
    """
    if not episodes:
        return "_No episodes to process._"
    lines: list[str] = []
    for i, ep in enumerate(episodes, 1):
        ts = ep.get("created_at", "unknown")
        butler = ep.get("butler", "unknown")
        content = ep.get("content", "")
        importance = ep.get("importance")
        header = f"### Episode {i}  ({butler}, {ts})"
        if importance is not None:
            header += f"  [importance={importance}]"
        lines.append(header)
        lines.append("")
        # Wrap episode content in XML tags to delimit untrusted data
        lines.append("<episode_content>")
        lines.append(content)
        lines.append("</episode_content>")
        lines.append("")
    return "\n".join(lines)


def _format_facts(facts: list[dict]) -> str:
    """Format existing facts for the dedup context section."""
    if not facts:
        return "_No existing facts._"
    lines: list[str] = []
    for f in facts:
        fid = f.get("id", "?")
        subj = f.get("subject", "?")
        pred = f.get("predicate", "?")
        content = f.get("content", "")
        perm = f.get("permanence", "?")
        lines.append(f"- **{fid}**: [{perm}] {subj} — {pred} — {content}")
    return "\n".join(lines)


def _format_rules(rules: list[dict]) -> str:
    """Format existing rules for the dedup context section."""
    if not rules:
        return "_No existing rules._"
    lines: list[str] = []
    for r in rules:
        rid = r.get("id", "?")
        content = r.get("content", "")
        status = r.get("status", "?")
        lines.append(f"- **{rid}**: [{status}] {content}")
    return "\n".join(lines)


def build_consolidation_prompt(
    episodes: list[dict],
    existing_facts: list[dict],
    existing_rules: list[dict],
    butler_name: str,
) -> str:
    """Build the full consolidation prompt for a runtime session.

    Reads the SKILL.md template and appends contextual sections containing
    the episodes to process, existing facts and rules for dedup reference,
    and the butler name for scoping.

    Args:
        episodes: Raw episode dicts to consolidate.
        existing_facts: Current facts (with IDs) for dedup.
        existing_rules: Current rules (with IDs) for dedup.
        butler_name: Name of the butler requesting consolidation.

    Returns:
        The complete prompt string ready for a runtime instance.
    """
    template = _SKILL_MD_PATH.read_text(encoding="utf-8")

    sections: list[str] = [
        template.rstrip(),
        "",
        "---",
        "",
        f"## Consolidation Context — {butler_name}",
        "",
        "## Episodes to Process",
        "",
        _format_episodes(episodes),
        "## Existing Facts (for dedup)",
        "",
        _format_facts(existing_facts),
        "",
        "## Existing Rules (for dedup)",
        "",
        _format_rules(existing_rules),
    ]

    return "\n".join(sections) + "\n"
