"""Unit tests for GET /api/chronicler/who-you-were-with (IEA, tasks.md S9b,
bu-jc6htw.2).

Mocked-pool tests: ``pool.fetch`` (chronicler's own v_episodes_corrected +
episode_entities read) and ``db.fan_out_with_status`` (the relationship-
butler-own-pool entity-name lookup) are both mocked, so these tests exercise
lane filtering, window clipping, channel derivation, the unattributed
bucket, and the companion_names_unavailable / who_you_were_with_source_error
degraded-envelope flags without a real database.

Per RFC 0014 §D17 the endpoint's own SQL (against v_episodes_corrected +
episode_entities) must stay inside the chronicler schema — see
tests/contracts/test_chronicler_no_cross_schema.py for the static guardrail
that enforces this; entity-name resolution runs through a SEPARATE pool via
db.fan_out_with_status, not the chronicler pool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ENDPOINT = "/api/chronicler/who-you-were-with"

_ENTITY_A = uuid4()
_ENTITY_B = uuid4()

_T0 = datetime(2026, 7, 5, 0, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 7, 6, 0, 0, 0, tzinfo=UTC)


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _episode_row(
    *,
    episode_id: str | None = None,
    source_name: str = "comms.message_bursts",
    episode_type: str = "social_episode",
    start_at: datetime,
    end_at: datetime | None = None,
    payload: dict | None = None,
    entity_id: Any = None,
    layer: str = "activity",
) -> _Row:
    return _Row(
        {
            "episode_id": episode_id or str(uuid4()),
            "source_name": source_name,
            "episode_type": episode_type,
            "start_at": start_at,
            "end_at": end_at,
            "payload": payload if payload is not None else {"channel": "telegram_bot"},
            "entity_id": entity_id,
            "layer": layer,
        }
    )


def _find_chronicler_router_module(app: Any) -> Any:
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler":
            return router_module
    raise AssertionError("chronicler router module not registered")


def _build_app(
    *,
    rows: list[_Row] | None = None,
    raise_error: bool = False,
    fan_out_results: dict[str, list[Any]] | None = None,
    fan_out_failed: list[str] | None = None,
):
    mock_pool = AsyncMock()
    if raise_error:
        mock_pool.fetch = AsyncMock(side_effect=RuntimeError("simulated query failure"))
    else:
        mock_pool.fetch = AsyncMock(return_value=rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.fan_out_with_status = AsyncMock(
        return_value=(fan_out_results or {}, fan_out_failed or [])
    )

    app = create_app()
    router_module = _find_chronicler_router_module(app)
    app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db

    return app, mock_db


async def _get(app: Any, params: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(_ENDPOINT, params=params)


_PARAMS = {"start_at": _T0.isoformat(), "end_at": _T1.isoformat()}


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


async def test_missing_params_returns_400():
    app, _ = _build_app()
    resp = await _get(app, {})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_parameter"


async def test_end_before_start_returns_400():
    app, _ = _build_app()
    resp = await _get(app, {"start_at": _T1.isoformat(), "end_at": _T0.isoformat()})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_time_range"


async def test_invalid_timezone_returns_400():
    app, _ = _build_app()
    resp = await _get(app, {**_PARAMS, "tz": "Not/A_Zone"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_timezone"


# ---------------------------------------------------------------------------
# Resolved companion
# ---------------------------------------------------------------------------


async def test_resolved_companion_returned_with_name_channel_and_duration():
    row = _episode_row(
        start_at=_T0.replace(hour=10),
        end_at=_T0.replace(hour=10, minute=30),
        payload={"channel": "telegram_bot"},
        entity_id=_ENTITY_A,
    )
    app, db = _build_app(
        rows=[row],
        fan_out_results={"relationship": [_Row({"id": _ENTITY_A, "canonical_name": "Alex"})]},
    )
    resp = await _get(app, _PARAMS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["who_you_were_with_source_error"] is False
    assert data["companion_names_unavailable"] is False
    assert len(data["companions"]) == 1
    companion = data["companions"][0]
    assert companion["entity_id"] == str(_ENTITY_A)
    assert companion["display_name"] == "Alex"
    assert companion["unattributed"] is False
    assert companion["channel"] == "Telegram"
    assert companion["co_present_seconds"] == pytest.approx(1800.0)
    assert companion["episode_count"] == 1

    # Entity-name lookup targeted the relationship butler's own pool, not
    # the chronicler pool.
    assert db.fan_out_with_status.await_args.kwargs["butler_names"] == ["relationship"]


async def test_channel_labels_mapped_for_each_comms_source():
    rows = [
        _episode_row(
            start_at=_T0.replace(hour=h),
            end_at=_T0.replace(hour=h, minute=5),
            payload={"channel": ch},
        )
        for h, ch in enumerate(["email", "whatsapp_user_client", "discord"], start=1)
    ]
    app, _ = _build_app(rows=rows)
    resp = await _get(app, _PARAMS)
    channels = {c["channel"] for c in resp.json()["data"]["companions"]}
    assert channels == {"email", "WhatsApp", "Discord"}


async def test_non_comms_payload_defaults_to_in_person_channel():
    row = _episode_row(
        start_at=_T0.replace(hour=10), end_at=_T0.replace(hour=10, minute=30), payload={}
    )
    app, _ = _build_app(rows=[row])
    resp = await _get(app, _PARAMS)
    companions = resp.json()["data"]["companions"]
    assert companions[0]["channel"] == "in-person"


# ---------------------------------------------------------------------------
# Unattributed participants
# ---------------------------------------------------------------------------


async def test_unresolved_participant_returned_as_unattributed_not_dropped():
    row = _episode_row(
        start_at=_T0.replace(hour=10), end_at=_T0.replace(hour=10, minute=15), entity_id=None
    )
    app, _ = _build_app(rows=[row])
    resp = await _get(app, _PARAMS)
    data = resp.json()["data"]
    assert len(data["companions"]) == 1
    companion = data["companions"][0]
    assert companion["entity_id"] is None
    assert companion["unattributed"] is True
    assert companion["display_name"] is None


# ---------------------------------------------------------------------------
# Lane filtering — only activity-layer, social-lane episodes count
# ---------------------------------------------------------------------------


async def test_non_social_lane_episode_excluded():
    row = _episode_row(
        source_name="core.sessions",
        episode_type="work",
        start_at=_T0.replace(hour=10),
        end_at=_T0.replace(hour=11),
        entity_id=_ENTITY_A,
        payload={"trigger_source": "route"},
    )
    app, _ = _build_app(rows=[row])
    resp = await _get(app, _PARAMS)
    assert resp.json()["data"]["companions"] == []


# ---------------------------------------------------------------------------
# Window clipping and duration union
# ---------------------------------------------------------------------------


async def test_open_ended_episode_clipped_to_window_end():
    row = _episode_row(start_at=_T0.replace(hour=23), end_at=None, entity_id=_ENTITY_A)
    app, _ = _build_app(rows=[row])
    resp = await _get(app, _PARAMS)
    companion = resp.json()["data"]["companions"][0]
    assert companion["co_present_seconds"] == pytest.approx(3600.0)


async def test_multiple_episodes_same_companion_and_channel_union_duration():
    rows = [
        _episode_row(
            start_at=_T0.replace(hour=9),
            end_at=_T0.replace(hour=9, minute=30),
            entity_id=_ENTITY_A,
        ),
        _episode_row(
            start_at=_T0.replace(hour=10),
            end_at=_T0.replace(hour=10, minute=30),
            entity_id=_ENTITY_A,
        ),
    ]
    app, _ = _build_app(rows=rows)
    resp = await _get(app, _PARAMS)
    companions = resp.json()["data"]["companions"]
    assert len(companions) == 1
    assert companions[0]["co_present_seconds"] == pytest.approx(3600.0)
    assert companions[0]["episode_count"] == 2


async def test_companions_sorted_by_duration_desc():
    rows = [
        _episode_row(
            start_at=_T0.replace(hour=8),
            end_at=_T0.replace(hour=8, minute=10),
            entity_id=_ENTITY_A,
        ),
        _episode_row(
            start_at=_T0.replace(hour=9),
            end_at=_T0.replace(hour=10),
            entity_id=_ENTITY_B,
        ),
    ]
    app, _ = _build_app(
        rows=rows,
        fan_out_results={
            "relationship": [
                _Row({"id": _ENTITY_A, "canonical_name": "Short"}),
                _Row({"id": _ENTITY_B, "canonical_name": "Long"}),
            ]
        },
    )
    resp = await _get(app, _PARAMS)
    companions = resp.json()["data"]["companions"]
    assert [c["display_name"] for c in companions] == ["Long", "Short"]


# ---------------------------------------------------------------------------
# Degraded-mode: source error vs. name-resolution failure
# ---------------------------------------------------------------------------


async def test_episode_query_failure_sets_source_error_and_empty_companions():
    app, _ = _build_app(raise_error=True)
    resp = await _get(app, _PARAMS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["who_you_were_with_source_error"] is True
    assert data["companions"] == []


async def test_relationship_lookup_failure_sets_names_unavailable_but_keeps_companion():
    row = _episode_row(
        start_at=_T0.replace(hour=10), end_at=_T0.replace(hour=10, minute=30), entity_id=_ENTITY_A
    )
    app, _ = _build_app(rows=[row], fan_out_results={}, fan_out_failed=["relationship"])
    resp = await _get(app, _PARAMS)
    data = resp.json()["data"]
    assert data["companion_names_unavailable"] is True
    assert data["who_you_were_with_source_error"] is False
    companion = data["companions"][0]
    assert companion["entity_id"] == str(_ENTITY_A)
    assert companion["unattributed"] is False
    assert companion["display_name"] is None
