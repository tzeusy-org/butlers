"""Repository policy checks for legacy unprefixed tool-name regressions."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF_PATH = Path(__file__).resolve().relative_to(REPO_ROOT)

_LEGACY_UNPREFIXED_TOOL_NAMES = frozenset(
    {
        "send" + "_message",
        "send" + "_email",
        "get" + "_updates",
        "reply" + "_to_message",
        "search" + "_inbox",
        "read" + "_email",
        "check" + "_and_route_inbox",
        "handle" + "_message",
    }
)
_LEGACY_NAME_PATTERN = re.compile(
    r"(?<![a-z0-9_])("
    + "|".join(sorted(map(re.escape, _LEGACY_UNPREFIXED_TOOL_NAMES)))
    + r")(?![a-z0-9_])"
)

_SCAN_ROOTS = (
    Path("src"),
    Path("roster"),
    Path("tests"),
    Path("docs"),
    Path("openspec"),
    Path(".github/prompts"),
    Path("AGENTS.md"),
    Path("README.md"),
)

_TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".txt",
}


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for rel_path in _SCAN_ROOTS:
        candidate = REPO_ROOT / rel_path
        if not candidate.exists():
            continue
        if candidate.is_file():
            if candidate.suffix in _TEXT_SUFFIXES or candidate.suffix == "":
                files.append(candidate)
            continue
        for file_path in sorted(candidate.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix not in _TEXT_SUFFIXES:
                continue
            files.append(file_path)
    return files


def test_legacy_unprefixed_tool_names_absent_from_repo_text_surfaces() -> None:
    """Legacy tool names must not appear as standalone tokens in repo text surfaces."""
    violations: list[str] = []

    for file_path in _iter_scan_files():
        rel_path = file_path.relative_to(REPO_ROOT)
        if rel_path == _SELF_PATH:
            continue

        content = file_path.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            match = _LEGACY_NAME_PATTERN.search(line)
            if match is None:
                continue
            violations.append(
                f"{rel_path}:{line_number}: found legacy tool token '{match.group(1)}'"
            )

    assert not violations, "Legacy unprefixed tool names found:\n" + "\n".join(violations)
