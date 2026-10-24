"""Converted deterministic schedules spawn no session (bu-idc5mx.4, AC1).

``upcoming-dates-check``, ``relationship-maintenance`` and ``chronicler_gap_interview`` are
job-mode rows in their butler.toml. Each is dispatched through
``background.dispatch_scheduled_task`` exactly as the scheduler does, with its real job body
running down to the data reads and the owner send, which are stubbed. The spawner is a sentinel
that records every attribute touched: the dispatcher may only probe ``codex_auth_authority``
(answered with AttributeError), and nothing may reach ``trigger`` or any other runtime surface.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.background import dispatch_scheduled_task

pytestmark = pytest.mark.unit

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


class _SpawnerSentinel:
    """A spawner that refuses every attribute, recording which ones were asked for."""

    def __init__(self) -> None:
        object.__setattr__(self, "touched", [])

    def __getattr__(self, name: str) -> Any:
        self.touched.append(name)
        raise AttributeError(f"spawner.{name} touched on a deterministic job path")


def _schedule_row(butler: str, name: str) -> dict[str, Any]:
    config = tomllib.loads((_ROSTER / butler / "butler.toml").read_text())
    rows = {row["name"]: row for row in config["butler"]["schedule"]}
    return rows[name]


def _stub_upcoming_dates(monkeypatch) -> AsyncMock:
    from butlers.tools.relationship import reminders

    monkeypatch.setattr(
        reminders,
        "upcoming_dates",
        AsyncMock(
            return_value=[
                {
                    "contact_name": "Alice",
                    "label": "Birthday",
                    "upcoming_date": datetime.now(UTC).date().isoformat(),
                }
            ]
        ),
    )
    send = AsyncMock(return_value="delivered")
    monkeypatch.setattr(reminders, "send_owner_telegram", send)
    return send


def _stub_maintenance(monkeypatch) -> AsyncMock:
    from butlers.tools.relationship import reminders

    monkeypatch.setattr(
        reminders,
        "contacts_overdue_evaluation",
        AsyncMock(
            return_value={"contacts": [], "cadence_available": True, "unmeasurable_count": 0}
        ),
    )
    send = AsyncMock(return_value="delivered")
    monkeypatch.setattr(reminders, "send_owner_telegram", send)
    return send


def _stub_gap_interview(monkeypatch) -> AsyncMock:
    from butlers.chronicler import gap_interview_job

    monkeypatch.setattr(
        "butlers.core.general_settings.resolve_general_timezone",
        AsyncMock(return_value="UTC"),
    )
    ask = AsyncMock(return_value={"status": "no_gap"})
    monkeypatch.setattr(gap_interview_job, "ask_gap_interview", ask)
    return ask


@pytest.mark.parametrize(
    ("butler", "schedule", "stub"),
    [
        ("relationship", "upcoming-dates-check", _stub_upcoming_dates),
        ("relationship", "relationship-maintenance", _stub_maintenance),
        ("chronicler", "chronicler_gap_interview", _stub_gap_interview),
    ],
    ids=["upcoming-dates-check", "relationship-maintenance", "chronicler_gap_interview"],
)
async def test_converted_schedule_runs_its_job_without_touching_the_spawner(
    monkeypatch, butler, schedule, stub
):
    row = _schedule_row(butler, schedule)
    assert row.get("dispatch_mode") == "job"
    assert "prompt" not in row
    effect = stub(monkeypatch)
    spawner = _SpawnerSentinel()

    await dispatch_scheduled_task(
        butler_name=butler,
        pool=MagicMock(),
        spawner=spawner,
        trigger_source=f"schedule:{schedule}",
        prompt=row.get("prompt"),
        job_name=row["job_name"],
        job_args=row.get("job_args"),
        switchboard_client=MagicMock(),
    )

    effect.assert_awaited_once()
    assert set(spawner.touched) <= {"codex_auth_authority"}
