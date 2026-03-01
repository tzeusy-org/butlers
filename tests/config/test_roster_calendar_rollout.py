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


def test_calendar_enabled_butlers_do_not_hardcode_calendar_id() -> None:
    """Calendar ID is resolved at runtime; it must NOT be in butler.toml."""
    for butler_name in CALENDAR_ENABLED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        calendar = modules.get("calendar")

        assert isinstance(calendar, dict), f"{butler_name} is missing [modules.calendar]"
        assert calendar.get("provider") == "google"
        assert calendar.get("conflicts", {}).get("policy") == "suggest"

        assert "calendar_id" not in calendar, (
            f"{butler_name} still has calendar_id in butler.toml; "
            "it should be resolved at runtime from the credential store"
        )


def _resolve_includes(path: Path) -> str:
    """Read a file and inline any ``@<relative-path>`` include directives."""
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


def test_calendar_enabled_butlers_document_conflict_and_v1_scope() -> None:
    """Calendar-enabled CLAUDE guidance should include conflict and scope constraints."""
    required_fragments = (
        "calendar_list_events/get_event/create_event/update_event",
        "shared butler calendar",
        "default conflict behavior is `suggest`",
        "attendee invites are out of scope for v1",
    )

    for butler_name in CALENDAR_ENABLED_BUTLERS:
        guidance_path = REPO_ROOT / "roster" / butler_name / "CLAUDE.md"
        guidance = _resolve_includes(guidance_path).lower()
        for fragment in required_fragments:
            assert fragment in guidance, f"{butler_name} missing guidance fragment: {fragment}"
