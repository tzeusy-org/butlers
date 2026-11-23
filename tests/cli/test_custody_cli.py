"""Host custody commands (bu-s11n0s.1 slice 2a): explicit confirm, printed command id, read back."""

from __future__ import annotations

import uuid

import pytest
from click.testing import CliRunner

from butlers.api.owner_auth import custody_cli
from butlers.cli import cli

pytestmark = pytest.mark.unit

FACT = str(uuid.uuid4())
HOLD = str(uuid.uuid4())


@pytest.fixture
def calls(monkeypatch):
    recorded: list[tuple[str, dict]] = []
    outcome: dict = {"result": {"outcome": "held"}}

    async def fake(action, payload):
        recorded.append((action, payload))
        if isinstance(outcome["result"], Exception):
            raise outcome["result"]
        return outcome["result"]

    monkeypatch.setattr(custody_cli, "run_custody_host", fake)
    return recorded, outcome


def test_hold_requires_confirm(calls) -> None:
    recorded, _ = calls
    result = CliRunner().invoke(cli, ["custody", "hold", "--fact", FACT, "--reason", "lost"])
    assert result.exit_code != 0
    assert recorded == []


def test_hold_prints_its_command_id_and_sends_a_confirmed_request(calls) -> None:
    recorded, _ = calls
    result = CliRunner().invoke(
        cli, ["custody", "hold", "--fact", FACT, "--reason", "stolen", "--confirm"]
    )
    assert result.exit_code == 0, result.output
    action, payload = recorded[0]
    assert action == "hold"
    assert payload["fact_ids"] == [FACT]
    assert payload["reason"] == "stolen"
    assert payload["confirm"] is True
    assert f"Command: {payload['command_id']}" in result.output
    uuid.UUID(payload["command_id"])


def test_release_sends_the_expected_generation(calls) -> None:
    recorded, outcome = calls
    outcome["result"] = {"outcome": "released"}
    result = CliRunner().invoke(
        cli, ["custody", "release", "--hold", HOLD, "--generation", "3", "--confirm"]
    )
    assert result.exit_code == 0, result.output
    assert recorded[0][0] == "release"
    assert recorded[0][1]["hold_id"] == HOLD
    assert recorded[0][1]["expected_generation"] == 3


def test_a_refusal_fails_the_command_with_its_reason(calls) -> None:
    _, outcome = calls
    outcome["result"] = {"outcome": "refused", "reason": "still_bound"}
    result = CliRunner().invoke(
        cli, ["custody", "replace", "--hold", HOLD, "--generation", "1", "--confirm"]
    )
    assert result.exit_code != 0
    assert "still_bound" in result.output


def test_a_lost_answer_points_at_the_read_back(calls) -> None:
    recorded, outcome = calls
    outcome["result"] = custody_cli.CustodyHostError()
    result = CliRunner().invoke(
        cli, ["custody", "hold", "--fact", FACT, "--reason", "lost", "--confirm"]
    )
    assert result.exit_code != 0
    command_id = recorded[0][1]["command_id"]
    assert f"butlers custody status --command {command_id}" in result.output
