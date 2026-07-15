"""Tests for deterministic OwnTracks Wi-Fi SSID presence projection."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.chronicler.adapters.owntracks_ssid import (
    DEFAULT_MAX_GAP_MINUTES,
    EPISODE_TYPE_HOME_PRESENCE,
    EPISODE_TYPE_WORK_PRESENCE,
    SOURCE_NAME,
    SSID_PLACE_STATE_KEY,
    OwnTracksSsidPresenceAdapter,
    group_ssid_points,
    parse_ssid_places,
)
from butlers.chronicler.models import Confidence, Layer, Precision, Privacy

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
_ENDPOINT = "owntracks:alice"


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _row(
    minute: int,
    *,
    ssid: str | None = "Corp WiFi",
    endpoint_identity: str = _ENDPOINT,
) -> dict:
    raw_payload = {"_type": "location"}
    if ssid is not None:
        raw_payload["SSID"] = ssid
    ts = _NOW + timedelta(minutes=minute)
    return {
        "id": uuid4(),
        "ts": ts,
        "endpoint_identity": endpoint_identity,
        "raw_payload": raw_payload,
        "recorded_at": ts,
    }


def test_parse_ssid_places_accepts_owner_state_mapping() -> None:
    assert parse_ssid_places({"Corp WiFi": "work", "Home WiFi": "home"}) == {
        "Corp WiFi": "work",
        "Home WiFi": "home",
    }


@pytest.mark.parametrize(
    "value,match",
    [
        (["Corp WiFi"], "JSON object"),
        ({"": "work"}, "non-empty SSID"),
        ({"Corp WiFi": "office"}, "home.*work"),
    ],
)
def test_parse_ssid_places_rejects_malformed_owner_state(value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_ssid_places(value)


def test_contiguous_same_ssid_points_form_one_presence_span() -> None:
    spans, carryover = group_ssid_points(
        [_row(0), _row(10), _row(20)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert len(spans) == 1
    assert spans[0].place == "work"
    assert spans[0].start_at == _NOW
    assert spans[0].end_at == _NOW + timedelta(minutes=20)
    assert spans[0].point_count == 3
    assert carryover[_ENDPOINT]["ssid"] == "Corp WiFi"


def test_gap_over_threshold_splits_same_ssid_into_two_presence_spans() -> None:
    spans, _ = group_ssid_points(
        [_row(0), _row(10), _row(90), _row(100)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10)),
        (_NOW + timedelta(minutes=90), _NOW + timedelta(minutes=100)),
    ]


def test_unlabelled_ssid_is_not_emitted_and_breaks_contiguity() -> None:
    spans, _ = group_ssid_points(
        [_row(0), _row(10), _row(20, ssid="Cafe WiFi"), _row(30), _row(40)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at, span.place) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10), "work"),
        (_NOW + timedelta(minutes=30), _NOW + timedelta(minutes=40), "work"),
    ]


def test_missing_ssid_breaks_contiguity_and_singletons_do_not_claim_presence() -> None:
    spans, carryover = group_ssid_points(
        [_row(0), _row(10, ssid=None), _row(20)],
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert spans == []
    assert carryover[_ENDPOINT]["point_count"] == 1


def test_json_string_raw_payload_is_decoded_for_grouping() -> None:
    rows = [_row(0), _row(10)]
    for row in rows:
        row["raw_payload"] = json.dumps(row["raw_payload"])

    spans, _ = group_ssid_points(
        rows,
        ssid_places={"Corp WiFi": "work"},
        max_gap=timedelta(minutes=DEFAULT_MAX_GAP_MINUTES),
    )

    assert [(span.start_at, span.end_at, span.place) for span in spans] == [
        (_NOW, _NOW + timedelta(minutes=10), "work")
    ]


async def test_empty_poll_preserves_endpoint_carryover() -> None:
    prior_carryover = {
        _ENDPOINT: {
            "ssid": "Corp WiFi",
            "start_at": _NOW.isoformat(),
            "end_at": _NOW.isoformat(),
            "point_count": 1,
        }
    }
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[])),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.save_carryover",
            new=AsyncMock(),
        ) as save,
    ):
        await adapter.project(AsyncMock(), chronicler_pool=AsyncMock(), since=_NOW)

    saved = save.await_args.args[2]
    assert saved[_ENDPOINT] == prior_carryover[_ENDPOINT]


async def test_all_malformed_batch_preserves_endpoint_carryover() -> None:
    prior_carryover = {
        _ENDPOINT: {
            "ssid": "Corp WiFi",
            "start_at": _NOW.isoformat(),
            "end_at": _NOW.isoformat(),
            "point_count": 1,
        }
    }
    malformed_row = _row(10, endpoint_identity="")
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.get_carryover",
            new=AsyncMock(return_value=prior_carryover),
        ),
        patch.object(adapter, "_fetch_points", new=AsyncMock(return_value=[malformed_row])),
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.save_carryover",
            new=AsyncMock(),
        ) as save,
    ):
        result = await adapter.project(AsyncMock(), chronicler_pool=AsyncMock(), since=_NOW)

    saved = save.await_args.args[2]
    assert saved[_ENDPOINT] == prior_carryover[_ENDPOINT]
    assert result.watermark == _NOW + timedelta(minutes=10)


async def test_upsert_work_presence_stamps_minute_activity_and_medium_confidence() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Corp WiFi": "work"})
    spans, _ = group_ssid_points(
        [_row(0), _row(15)],
        ssid_places=adapter.ssid_places,
        max_gap=adapter.max_gap,
    )
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncCtx(conn)
    owner_id = uuid4()

    async def _return_episode(_conn: object, episode: object) -> object:
        episode.id = uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_episode",
            side_effect=_return_episode,
        ) as upsert,
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_owner_episode_entity",
            new=AsyncMock(),
        ) as owner_link,
    ):
        episode = await adapter._upsert_presence_episode(pool, spans[0], entity_id=owner_id)

    projected = upsert.await_args.args[1]
    assert projected.episode_type == EPISODE_TYPE_WORK_PRESENCE
    assert projected.precision == Precision.MINUTE
    assert projected.layer == Layer.ACTIVITY
    assert projected.confidence == Confidence.MEDIUM
    assert projected.privacy == Privacy.NORMAL
    assert projected.payload == {
        "place": "work",
        "point_count": 2,
        "endpoint_identity": _ENDPOINT,
    }
    assert "Corp WiFi" not in str(projected.payload)
    assert "Corp WiFi" not in projected.source_ref
    owner_link.assert_awaited_once_with(conn, episode.id, owner_id=owner_id)


async def test_upsert_home_presence_uses_distinct_episode_type() -> None:
    adapter = OwnTracksSsidPresenceAdapter(ssid_places={"Home WiFi": "home"})
    spans, _ = group_ssid_points(
        [_row(0, ssid="Home WiFi"), _row(5, ssid="Home WiFi")],
        ssid_places=adapter.ssid_places,
        max_gap=adapter.max_gap,
    )
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value = _AsyncCtx(conn)

    async def _return_episode(_conn: object, episode: object) -> object:
        episode.id = uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_episode",
            side_effect=_return_episode,
        ) as upsert,
        patch(
            "butlers.chronicler.adapters.owntracks_ssid.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
    ):
        await adapter._upsert_presence_episode(pool, spans[0], entity_id=None)

    assert upsert.await_args.args[1].episode_type == EPISODE_TYPE_HOME_PRESENCE


def test_ssid_adapter_has_no_llm_imports() -> None:
    import butlers.chronicler.adapters.owntracks_ssid as mod

    source_path = Path(mod.__file__ or "")
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    forbidden = ("anthropic", "openai", "langchain", "litellm", "llm")
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith(forbidden)]


def test_state_key_is_scoped_to_chronicler_owntracks_config() -> None:
    assert SSID_PLACE_STATE_KEY == "chronicler/owntracks/ssid_places"


def test_source_and_episode_identifiers_are_distinct_from_gps_place_cluster() -> None:
    assert SOURCE_NAME == "owntracks.ssid_presence"
    assert EPISODE_TYPE_HOME_PRESENCE != "place_episode"
    assert EPISODE_TYPE_WORK_PRESENCE != "place_episode"
