"""AGENTS.md and CLAUDE.md stay small and point only at paths that exist (bu-99t5s3.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
GUIDANCE = {"AGENTS.md": 300, "CLAUDE.md": 130}
REPO_PREFIXES = (
    ".claude/", ".github/", "about/", "alembic/", "docs/", "frontend/", "openspec/", "roster/",
    "scripts/", "src/", "tests/", "Makefile", "pyproject.toml",
)  # fmt: skip
# Gitignored or hypothetical paths the guidance names on purpose: the bd export mirror, a file the
# text says never to create, and the untracked npm install tree.
ALLOWLIST = {".beads/issues.jsonl", ".beads/issues.export.jsonl", "frontend/node_modules"}
CODE_SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _repo_paths(text: str) -> set[str]:
    found = set()
    for token in [*CODE_SPAN.findall(text), *LINK.findall(text)]:
        token = re.split(r"[#:]", token.split()[0], maxsplit=1)[0] if token.strip() else ""
        if token.startswith(REPO_PREFIXES) and not re.search(r"[<>{}*$|]|\.\.\.", token):
            found.add(token.rstrip("/"))
    return found - ALLOWLIST


@pytest.mark.parametrize(("name", "cap"), GUIDANCE.items())
def test_guidance_file_stays_within_its_line_cap(name: str, cap: int) -> None:
    lines = (ROOT / name).read_text(encoding="utf-8").count("\n")
    assert lines <= cap, f"{name} has {lines} lines (cap {cap}): move detail into docs/"


@pytest.mark.parametrize("name", GUIDANCE)
def test_guidance_file_cites_only_existing_paths(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    paths = _repo_paths(text)
    assert paths, f"{name}: path extraction found nothing, the check is blind"
    dead = sorted(p for p in paths if not (ROOT / p).exists())
    assert not dead, f"{name} cites paths that do not exist: {dead}"
