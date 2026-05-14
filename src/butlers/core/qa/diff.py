"""Unified diff parsing for QA investigation snapshots."""

from __future__ import annotations

from butlers.core.qa.notes import DiffLine

_META_PREFIXES = ("diff --git", "index ", "--- ", "+++ ", "@@")


def parse_unified_diff(text: str, max_lines: int = 10_000) -> list[DiffLine]:
    """Parse a unified diff into the compact QA dossier snapshot shape."""

    if max_lines < 0:
        raise ValueError("max_lines must be non-negative")

    raw_lines = text.splitlines()
    visible_lines = raw_lines[:max_lines]
    parsed: list[DiffLine] = []
    for line in visible_lines:
        if line.startswith(_META_PREFIXES):
            parsed.append(DiffLine(kind="meta", text=line))
        elif line.startswith("+"):
            parsed.append(DiffLine(kind="+", text=line[1:]))
        elif line.startswith("-"):
            parsed.append(DiffLine(kind="-", text=line[1:]))
        elif line.startswith(" "):
            parsed.append(DiffLine(kind=" ", text=line[1:]))
        else:
            parsed.append(DiffLine(kind="meta", text=line))

    omitted = len(raw_lines) - len(visible_lines)
    if omitted > 0:
        parsed.append(DiffLine(kind="meta", text=f"... (truncated, {omitted} more lines)"))
    return parsed
