"""Contracts for General scheduled prompts that rely on notify()."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_eod_tomorrow_prep_notify_includes_message_argument() -> None:
    """The general EOD prep schedule must instruct notify() with an explicit message payload."""
    toml_path = Path(__file__).resolve().parents[2] / "roster" / "general" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config["butler"]["schedule"]
    eod_schedule = next(s for s in schedules if s["name"] == "eod-tomorrow-prep")
    prompt = eod_schedule["prompt"]

    assert 'notify(channel="telegram", intent="send"' in prompt
    assert "message=" in prompt
