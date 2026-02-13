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


def test_calendar_enabled_butlers_have_dedicated_subcalendar_ids() -> None:
    """Calendar-enabled roster butlers should declare dedicated subcalendar IDs."""
    for butler_name in CALENDAR_ENABLED_BUTLERS:
        modules = _load_butler_toml(butler_name).get("modules", {})
        calendar = modules.get("calendar")

        assert isinstance(calendar, dict), f"{butler_name} is missing [modules.calendar]"
        assert calendar.get("provider") == "google"
        assert calendar.get("default_conflict_policy") == "suggest"

        calendar_id = calendar.get("calendar_id")
        assert isinstance(calendar_id, str) and calendar_id.strip()
        assert calendar_id != "primary"
        assert "@group.calendar.google.com" in calendar_id


def test_calendar_enabled_butlers_document_conflict_and_v1_scope() -> None:
    """Calendar-enabled CLAUDE guidance should include conflict and scope constraints."""
    required_fragments = (
        "calendar_list_events/get_event/create_event/update_event",
        "dedicated butler subcalendar",
        "default conflict behavior is `suggest`",
        "attendee invites are out of scope for v1",
    )

    for butler_name in CALENDAR_ENABLED_BUTLERS:
        guidance_path = REPO_ROOT / "roster" / butler_name / "CLAUDE.md"
        guidance = guidance_path.read_text().lower()
        for fragment in required_fragments:
            assert fragment in guidance, f"{butler_name} missing guidance fragment: {fragment}"
