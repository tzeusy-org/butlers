"""Regression tests for roster calendar rollout configuration and guidance."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CALENDAR_ENABLED_BUTLERS = ("general", "health", "relationship")


def _load_butler_toml(butler_name: str) -> dict:
    path = REPO_ROOT / "roster" / butler_name / "butler.toml"
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _resolve_includes(path: Path) -> str:
    lines: list[str] = []
    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("@"):
            ref = path.parent / stripped[1:]
            if ref.is_file():
                lines.append(_resolve_includes(ref))
                continue
        lines.append(raw_line)
    return "\n".join(lines)


def test_calendar_rollout_config_and_guidance() -> None:
    """Calendar-enabled butlers: no hardcoded calendar_id; CLAUDE guidance is present."""
    for butler_name in CALENDAR_ENABLED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        calendar = modules.get("calendar")
        assert isinstance(calendar, dict), f"{butler_name} missing [modules.calendar]"
        assert calendar.get("provider") == "google"
        assert "calendar_id" not in calendar

        guidance = _resolve_includes(REPO_ROOT / "roster" / butler_name / "CLAUDE.md").lower()
        assert "default conflict behavior is `suggest`" in guidance
