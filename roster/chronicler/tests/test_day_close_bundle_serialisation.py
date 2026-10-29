"""Regression coverage for JSON-safe dropped-intent serialization (bu-gh9yq).

``chronicler_day_close_bundle`` returns its result dict directly as an MCP
tool response. ``reconciled.dropped_intents`` carries raw ``datetime``
objects on ``DroppedIntent.intent`` (the canonical reconciled episode dict),
unlike ``episodes``/``events`` which are normalized to ISO-8601 strings by
``bundle_assembler._serialise_items``. Without conversion, a day with a
dropped calendar intent would return a payload containing raw ``datetime``
objects that JSON serialization of the tool result would reject.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastmcp import FastMCP

from butlers.chronicler.reconciliation import DroppedIntent
from butlers.testing.mcp import registered_tools
from roster.chronicler.modules import _serialise_dropped_intents

pytestmark = pytest.mark.unit


def test_serialise_dropped_intents_converts_datetimes_to_iso_strings() -> None:
    start = datetime(2026, 6, 1, 9, tzinfo=UTC)
    end = datetime(2026, 6, 1, 10, tzinfo=UTC)
    dropped = DroppedIntent(
        intent={
            "canonical_title": "Gym",
            "canonical_start_at": start,
            "canonical_end_at": end,
        },
        contradicting_activity={"source_name": "home_assistant.history"},
        overlap_fraction=1.0,
    )

    [payload] = _serialise_dropped_intents([dropped])

    assert payload["start_at"] == start.isoformat()
    assert payload["end_at"] == end.isoformat()
    # The whole point: this must not raise TypeError on raw datetime objects.
    json.dumps(payload)


def test_serialise_dropped_intents_passes_through_non_datetime_values() -> None:
    """String-shaped windows (e.g. already-ISO rows) are left untouched."""
    dropped = DroppedIntent(
        intent={
            "title": "Gym",
            "start_at": "2026-06-01T09:00:00+00:00",
            "end_at": "2026-06-01T10:00:00+00:00",
        },
        contradicting_activity={"source_name": "home_assistant.history"},
        overlap_fraction=0.75,
        reason="custom reason",
    )

    [payload] = _serialise_dropped_intents([dropped])

    assert payload == {
        "title": "Gym",
        "start_at": "2026-06-01T09:00:00+00:00",
        "end_at": "2026-06-01T10:00:00+00:00",
        "reason": "custom reason",
        "overlap_fraction": 0.75,
    }


def test_serialise_dropped_intents_empty_list_is_empty() -> None:
    assert _serialise_dropped_intents([]) == []


def _corrected_episode(*, privacy, layer, source_ref, **changes):
    from uuid import UUID

    from butlers.chronicler.models import Confidence, CorrectedEpisode, Precision, Privacy

    start = datetime(2026, 6, 1, 9, tzinfo=UTC)
    values = dict(
        id=UUID(int=1),
        source_name="home_assistant.history",
        source_ref=source_ref,
        episode_type="presence_episode",
        start_at=start,
        end_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
        precision=Precision.EXACT,
        title="Visible block",
        payload={},
        privacy=privacy,
        retention_days=None,
        tombstone_at=None,
        canonical_start_at=start,
        canonical_end_at=datetime(2026, 6, 1, 10, tzinfo=UTC),
        canonical_title="Visible block",
        canonical_privacy=Privacy.NORMAL,
        corrected_at=start,
        correction_note=None,
        created_at=start,
        updated_at=start,
        layer=layer,
        confidence=Confidence.LOW,
    )
    values.update(changes)
    return CorrectedEpisode(**values)


async def _registered_bundle(monkeypatch, episodes, events):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from butlers.chronicler import storage
    from roster.chronicler.modules import ChroniclerModule

    mcp = FastMCP("chronicler-bundle-test")

    episode_reader = AsyncMock(return_value=episodes)
    event_reader = AsyncMock(return_value=events)
    monkeypatch.setattr(storage, "list_episodes", episode_reader)
    monkeypatch.setattr(storage, "list_point_events", event_reader)
    module = ChroniclerModule()
    pool = object()
    await module.register_tools(mcp, None, SimpleNamespace(pool=pool), "chronicler")
    tools = await registered_tools(mcp)
    result = await tools["chronicler_day_close_bundle"]("2026-06-01", "Asia/Singapore")
    episode_reader.assert_awaited_once_with(
        pool,
        start_from=datetime(2026, 5, 31, 16, tzinfo=UTC),
        start_to=datetime(2026, 6, 1, 16, tzinfo=UTC),
        limit=500,
    )
    event_reader.assert_awaited_once_with(
        pool,
        occurred_from=datetime(2026, 5, 31, 16, tzinfo=UTC),
        occurred_to=datetime(2026, 6, 1, 16, tzinfo=UTC),
        limit=500,
    )
    return result


async def test_registered_day_close_preserves_masked_envelopes_without_derived_payload(
    monkeypatch,
) -> None:
    from uuid import UUID

    from butlers.chronicler.models import (
        Confidence,
        CorrectedPointEvent,
        Layer,
        Precision,
        Privacy,
    )

    normal = _corrected_episode(
        privacy=Privacy.NORMAL, layer=Layer.ACTIVITY, source_ref="normal:visible"
    )
    sensitive = _corrected_episode(
        privacy=Privacy.SENSITIVE,
        layer=Layer.ACTIVITY,
        source_ref="MASKED_REF_CANARY",
        title="MASKED_TITLE_CANARY",
        canonical_title="MASKED_CANONICAL_CANARY",
        end_at=datetime(2026, 6, 1, 11, tzinfo=UTC),
        confidence=Confidence.HIGH,
        payload={"context_name": "MASKED_PAYLOAD_CANARY", "lat": 1, "lon": 2},
        evidence_refs=["MASKED_EVIDENCE_CANARY"],
    )
    restricted = _corrected_episode(
        privacy=Privacy.RESTRICTED,
        layer=Layer.ACTIVITY,
        source_ref="RESTRICTED_REF_CANARY",
        title="RESTRICTED_TITLE_CANARY",
        end_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
        canonical_end_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
    )
    intent = _corrected_episode(
        privacy=Privacy.NORMAL,
        layer=Layer.INTENT,
        source_ref="normal:planned",
        source_name="calendar.events",
        episode_type="calendar_event",
        start_at=datetime(2026, 6, 1, 11, tzinfo=UTC),
        end_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
        canonical_start_at=datetime(2026, 6, 1, 11, tzinfo=UTC),
        canonical_end_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
    )
    event_at = datetime(2026, 6, 1, 13, tzinfo=UTC)
    event = CorrectedPointEvent(
        id=UUID(int=2),
        source_name="health.meals",
        source_ref="MASKED_EVENT_REF_CANARY",
        event_type="eating_event",
        occurred_at=event_at,
        precision=Precision.EXACT,
        title="MASKED_EVENT_TITLE_CANARY",
        payload={"arbitrary": "MASKED_EVENT_PAYLOAD_CANARY"},
        privacy=Privacy.SENSITIVE,
        retention_days=None,
        tombstone_at=None,
        canonical_occurred_at=event_at,
        canonical_title="MASKED_EVENT_TITLE_CANARY",
        canonical_privacy=Privacy.NORMAL,
        corrected_at=event_at,
        correction_note=None,
        created_at=event_at,
        updated_at=event_at,
    )

    result = await _registered_bundle(monkeypatch, [normal, sensitive, restricted, intent], [event])

    assert result["sensitive_envelopes"] == [
        {
            "start_at": "2026-06-01T17:00:00+08:00",
            "end_at": "2026-06-01T19:00:00+08:00",
            "category": "home",
            "duration": 7200.0,
        },
        {
            "start_at": "2026-06-01T21:00:00+08:00",
            "end_at": "2026-06-01T21:00:00+08:00",
            "category": "meal",
            "duration": 0.0,
        },
    ]
    assert [row["source_ref"] for row in result["episodes"]] == [
        "normal:planned",
        "normal:visible",
    ]
    visible = next(row for row in result["episodes"] if row["source_ref"] == "normal:visible")
    assert visible["end_at"] == normal.end_at.isoformat()
    assert result["events"] == []
    assert result["citations"] == ["normal:planned", "normal:visible"]
    assert result.get("dropped_intents", []) == []
    assert "CANARY" not in json.dumps(result)


async def test_registered_envelope_only_bundle_matches_prompt_contract(monkeypatch) -> None:
    import tomllib
    from pathlib import Path

    from butlers.chronicler.models import Layer, Privacy

    row = _corrected_episode(
        privacy=Privacy.SENSITIVE, layer=Layer.ACTIVITY, source_ref="MASKED_ONLY_CANARY"
    )
    result = await _registered_bundle(monkeypatch, [row], [])
    assert result["episodes"] == []
    assert result["events"] == []
    assert result["citations"] == []
    assert len(result["sensitive_envelopes"]) == 1
    config = tomllib.loads((Path(__file__).parents[1] / "butler.toml").read_text())
    schedule = next(s for s in config["butler"]["schedule"] if s["name"] == "chronicler_day_close")
    assert "no episodes, no events, and no sensitive_envelopes" in schedule["prompt"]
    assert "CANARY" not in json.dumps(result)
