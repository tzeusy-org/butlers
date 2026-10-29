"""calendar_respond's tool metadata must pass the daemon's catalog check (bu-uy5zdu).

Messenger and Relationship enable Calendar responses in their roster config, and
both failed to start with "incomplete tool presentation metadata: calendar_respond"
because the module declared half of the presentation fields. Conditional response
registration owns its complete descriptor through the module metadata channel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import load_config
from butlers.core.tool_catalog import validate_tool_metadata
from butlers.modules.calendar import CalendarConfig, CalendarModule

pytestmark = pytest.mark.unit

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


@pytest.mark.parametrize("butler_name", ["messenger", "relationship"])
def test_roster_response_config_metadata_passes_catalog_validation(butler_name: str) -> None:
    calendar_config = load_config(_ROSTER / butler_name).modules["calendar"]
    assert calendar_config.get("response_enabled") is True
    module = CalendarModule()
    module._config = CalendarConfig(**calendar_config)

    metadata = module.tool_metadata()["calendar_respond"]

    validate_tool_metadata("calendar_respond", "calendar", metadata)
    assert metadata.arg_sensitivities["_write"] is True
    assert metadata.group_name == "response"
    assert calendar_config["groups"] == (
        ["response"] if butler_name == "messenger" else ["core", "response"]
    )
