"""Synthetic registered-tool composition; no owner data, database or model."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastmcp import FastMCP

from butlers.chronicler import storage
from butlers.chronicler.models import Confidence, CorrectedEpisode, Layer, Precision, Privacy
from butlers.roster_imports import roster_module
from butlers.testing.mcp import registered_tools

pytestmark = pytest.mark.unit


async def test_registered_bundle_composes_owner_day_with_canonical_enums(monkeypatch):
    day = datetime(2026, 10, 15, tzinfo=ZoneInfo("Asia/Singapore"))
    template = CorrectedEpisode(
        id=UUID(int=1),
        source_name="google_health.measurements",
        source_ref="synthetic:sleep",
        episode_type="sleep_episode",
        start_at=day + timedelta(hours=1),
        end_at=day + timedelta(hours=7),
        precision=Precision.MINUTE,
        title="Sleep",
        payload={},
        privacy=Privacy.NORMAL,
        retention_days=None,
        tombstone_at=None,
        canonical_start_at=day + timedelta(hours=1),
        canonical_end_at=day + timedelta(hours=7),
        canonical_title="Sleep",
        canonical_privacy=Privacy.NORMAL,
        corrected_at=None,
        correction_note=None,
        created_at=day,
        updated_at=day,
        layer=Layer.ACTIVITY,
        confidence=Confidence.MEDIUM,
    )
    rows = [template]
    for index, (source, kind, start, end, title, payload) in enumerate(
        [
            ("spotify.session_summary", "listening_episode", 0, 8, "Playlist", {}),
            ("owntracks.ssid_presence", "presence_episode", 8, 9, "Home", {"place": "home"}),
            ("owntracks.points", "movement_episode", 9, 10, "Movement (71 points)", {}),
            (
                "comms.message_bursts",
                "social_episode",
                13,
                14,
                "Messages with Mail Sender",
                {"channel": "email"},
            ),
            (
                "core.sessions",
                "work",
                15,
                16,
                "Conversation with Mail Sender",
                {"trigger_source": "route"},
            ),
        ],
        start=2,
    ):
        rows.append(
            replace(
                template,
                id=UUID(int=index),
                source_name=source,
                source_ref=f"synthetic:{index}",
                episode_type=kind,
                title=title,
                canonical_title=title,
                payload=payload,
                start_at=day + timedelta(hours=start),
                canonical_start_at=day + timedelta(hours=start),
                end_at=day + timedelta(hours=end),
                canonical_end_at=day + timedelta(hours=end),
                confidence=Confidence.LOW,
            )
        )
    episode_reader = AsyncMock(return_value=rows)
    event_reader = AsyncMock(return_value=[])
    monkeypatch.setattr(storage, "list_episodes", episode_reader)
    monkeypatch.setattr(storage, "list_point_events", event_reader)
    module = roster_module("chronicler", "modules").ChroniclerModule()
    mcp = FastMCP("synthetic-owner-day")
    pool = object()
    await module.register_tools(mcp, None, SimpleNamespace(pool=pool), "chronicler")
    tools = await registered_tools(mcp)
    result = await tools["chronicler_day_close_bundle"]("2026-10-15", "Asia/Singapore")

    for reader, begin, end in (
        (episode_reader, "start_from", "start_to"),
        (event_reader, "occurred_from", "occurred_to"),
    ):
        reader.assert_awaited_once_with(
            pool,
            **{begin: day.astimezone(UTC), end: (day + timedelta(days=1)).astimezone(UTC)},
            limit=500,
        )
    observations = result["owner_day"]["observations"]
    assert {row["kind"] for row in observations} >= {"sleep", "place", "movement", "playback"}
    text = "\n".join(row["text"] for row in observations)
    assert "home" in text and "background" in text and "Tentative" in text
    assert "Mail Sender" not in text and "71 points" not in text
    assert all(row["start_at"].endswith("+08:00") for row in observations)
    assert {ref for row in observations for ref in row["source_refs"]} <= set(result["citations"])
    assert result["diagnostics"] == {"automated_session_records": 1, "inbound_email_records": 1}
