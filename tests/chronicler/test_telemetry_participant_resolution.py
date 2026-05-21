"""Tests for bu-qlce5 telemetry additions.

Covers:
- §8.1: chronicler_episode_participants_resolved_total counter increments by
  the number of participant rows after a project() run with N participants.
- §8.2: list_episodes handler sets chronicler.episodes.filter_kind span
  attribute to "participant_join" when participant_entity_id is supplied,
  "owner_only" when entity_id is supplied, and "none" when neither is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from prometheus_client import REGISTRY

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
from butlers.chronicler.models import Episode

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Shared helpers — adapter
# ---------------------------------------------------------------------------


class _Row(dict):
    """asyncpg.Record-like dict subclass."""

    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _make_row(*, event_title: str = "Meeting") -> _Row:
    return _Row(
        {
            "id": uuid4(),
            "event_id": uuid4(),
            "source_id": uuid4(),
            "origin_instance_ref": f"evt:test:{uuid4()}",
            "starts_at": _NOW - timedelta(hours=1),
            "ends_at": _NOW,
            "status": "confirmed",
            "timezone": "UTC",
            "metadata": {},
            "updated_at": _NOW,
            "event_title": event_title,
            "event_description": None,
            "event_location": None,
        }
    )


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        return None


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _get_counter_value(schema: str) -> float:
    """Return current value of chronicler_episode_participants_resolved_total for schema."""
    val = REGISTRY.get_sample_value(
        "chronicler_episode_participants_resolved_total",
        {"schema": schema},
    )
    return val if val is not None else 0.0


# ---------------------------------------------------------------------------
# §8.1: Counter increments by participant count per episode
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_counter_increments_by_participant_count_in_project_run() -> None:
    """project() increments the counter by total participant count after projection.

    With 2 participants per episode and 1 episode, the counter should go up by 2.
    The owner row is NOT counted (it is always written but is not a participant
    in the multi-entity sense tracked by this counter).
    """
    schema = f"test_schema_{uuid4().hex[:8]}"
    owner_id = uuid4()
    participant_a = uuid4()
    participant_b = uuid4()
    row = _make_row(event_title="Team sync")

    before = _get_counter_value(schema)

    adapter = CalendarCompletedAdapter(butler_schemas=(schema,))
    episode_id = uuid4()

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
            entity_id=episode.entity_id,
        )

    # event_entities maps event_id → participant list (excludes owner)
    event_entities = {row["event_id"]: [participant_a, participant_b]}

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(return_value=[row])),
        patch.object(adapter, "_resolve_schema_entity_id", new=AsyncMock(return_value=owner_id)),
        patch.object(adapter, "_fetch_event_entities", new=AsyncMock(return_value=event_entities)),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    after = _get_counter_value(schema)
    assert after - before == 2.0, (
        f"Expected counter to increase by 2 (2 participants), got {after - before}"
    )


@pytest.mark.unit
async def test_counter_not_incremented_when_no_participants() -> None:
    """project() does NOT increment the counter when there are no participants.

    Owner-only episodes (empty participant_ids) produce zero counter increments.
    """
    schema = f"test_schema_{uuid4().hex[:8]}"
    owner_id = uuid4()
    row = _make_row(event_title="Solo session")

    before = _get_counter_value(schema)

    adapter = CalendarCompletedAdapter(butler_schemas=(schema,))
    episode_id = uuid4()

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return Episode(
            id=episode_id,
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
            entity_id=episode.entity_id,
        )

    # No participants in event_entities (empty list for this event_id).
    event_entities: dict[UUID, list[UUID]] = {}

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(return_value=[row])),
        patch.object(adapter, "_resolve_schema_entity_id", new=AsyncMock(return_value=owner_id)),
        patch.object(adapter, "_fetch_event_entities", new=AsyncMock(return_value=event_entities)),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    after = _get_counter_value(schema)
    assert after == before, (
        "Counter must not increment when there are no participants (owner-only episode)"
    )


@pytest.mark.unit
async def test_counter_increments_by_total_across_multiple_episodes() -> None:
    """project() increments the counter by total participants across all episodes.

    With 2 episodes: 1 participant and 3 participants = total 4 increment.
    """
    schema = f"test_schema_{uuid4().hex[:8]}"
    owner_id = uuid4()
    row1 = _make_row(event_title="Meeting A")
    row2 = _make_row(event_title="Meeting B")
    # Unique origin refs to bypass dedup.
    row1["origin_instance_ref"] = f"evt:a:{uuid4()}"
    row2["origin_instance_ref"] = f"evt:b:{uuid4()}"

    participant_a = uuid4()
    participant_b = uuid4()
    participant_c = uuid4()
    participant_d = uuid4()

    before = _get_counter_value(schema)

    adapter = CalendarCompletedAdapter(butler_schemas=(schema,))

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        return Episode(
            id=uuid4(),  # unique per call so _upsert_episode_entities works cleanly
            source_name=episode.source_name,
            source_ref=episode.source_ref,
            episode_type=episode.episode_type,
            start_at=episode.start_at,
            end_at=episode.end_at,
            precision=episode.precision,
            title=episode.title,
            payload=episode.payload,
            privacy=episode.privacy,
            entity_id=episode.entity_id,
        )

    event_entities = {
        row1["event_id"]: [participant_a],
        row2["event_id"]: [participant_b, participant_c, participant_d],
    }

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(return_value=[row1, row2])),
        patch.object(adapter, "_resolve_schema_entity_id", new=AsyncMock(return_value=owner_id)),
        patch.object(adapter, "_fetch_event_entities", new=AsyncMock(return_value=event_entities)),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_chronicler_pool(),
            since=None,
        )

    assert result.rows_projected == 2
    after = _get_counter_value(schema)
    assert after - before == 4.0, (
        f"Expected counter to increase by 4 (1 + 3 participants), got {after - before}"
    )


@pytest.mark.unit
def test_counter_is_exported_in_all_list() -> None:
    """chronicler_episode_participants_resolved_total must be in the module's __all__."""
    from butlers.chronicler.adapters import calendar as cal_module

    assert "chronicler_episode_participants_resolved_total" in cal_module.__all__
    assert cal_module.chronicler_episode_participants_resolved_total is not None


# ---------------------------------------------------------------------------
# §8.2: OTel span attribute for list_episodes filter_kind
# ---------------------------------------------------------------------------


_NOW_API = datetime.now(UTC)
_ENTITY_X = uuid4()
_ENTITY_Y = uuid4()


class _ApiRow(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _episode_api_row(
    *,
    entity_id: UUID | None = None,
    participant_entity_ids: list[UUID] | None = None,
) -> _ApiRow:
    return _ApiRow(
        {
            "id": uuid4(),
            "source_name": "google_calendar.completed",
            "source_ref": "calendar:ref",
            "episode_type": "scheduled_block",
            "start_at": _NOW_API - timedelta(hours=1),
            "end_at": _NOW_API,
            "precision": "exact",
            "title": "Team meeting",
            "payload": {},
            "privacy": "normal",
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": _NOW_API - timedelta(hours=1),
            "canonical_end_at": _NOW_API,
            "canonical_title": "Team meeting",
            "canonical_privacy": "normal",
            "corrected_at": None,
            "correction_note": None,
            "created_at": _NOW_API - timedelta(hours=2),
            "updated_at": _NOW_API,
            "entity_id": entity_id,
            "participant_entity_ids": participant_entity_ids or [],
        }
    )


def _build_api_app(rows: list[_ApiRow]):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    pool.fetchval = AsyncMock(return_value=len(rows))
    pool.execute = AsyncMock(return_value="OK")

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "chronicler" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    return app


class _FakeSpan:
    """Captures set_attribute calls for test assertions."""

    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def __enter__(self) -> _FakeSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _FakeTracer:
    def __init__(self, span: _FakeSpan) -> None:
        self._span = span

    def start_as_current_span(self, name: str) -> _FakeSpan:
        return self._span


@pytest.mark.unit
async def test_list_episodes_span_attribute_participant_join() -> None:
    """list_episodes sets filter_kind=participant_join when participant_entity_id is supplied."""
    rows = [_episode_api_row(participant_entity_ids=[_ENTITY_X])]
    app = _build_api_app(rows)

    fake_span = _FakeSpan()
    fake_tracer = _FakeTracer(fake_span)

    with patch("opentelemetry.trace.get_tracer", return_value=fake_tracer):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/chronicler/episodes?participant_entity_id={_ENTITY_X}")

    assert resp.status_code == 200, resp.text
    assert fake_span.attributes.get("chronicler.episodes.filter_kind") == "participant_join", (
        f"Expected filter_kind='participant_join', got: {fake_span.attributes}"
    )


@pytest.mark.unit
async def test_list_episodes_span_attribute_owner_only() -> None:
    """list_episodes sets filter_kind=owner_only when entity_id is supplied without participant_entity_id."""
    rows = [_episode_api_row(entity_id=_ENTITY_X)]
    app = _build_api_app(rows)

    fake_span = _FakeSpan()
    fake_tracer = _FakeTracer(fake_span)

    with patch("opentelemetry.trace.get_tracer", return_value=fake_tracer):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/chronicler/episodes?entity_id={_ENTITY_X}")

    assert resp.status_code == 200, resp.text
    assert fake_span.attributes.get("chronicler.episodes.filter_kind") == "owner_only", (
        f"Expected filter_kind='owner_only', got: {fake_span.attributes}"
    )


@pytest.mark.unit
async def test_list_episodes_span_attribute_none() -> None:
    """list_episodes sets filter_kind=none when neither entity_id nor participant_entity_id is supplied."""
    rows = [_episode_api_row()]
    app = _build_api_app(rows)

    fake_span = _FakeSpan()
    fake_tracer = _FakeTracer(fake_span)

    with patch("opentelemetry.trace.get_tracer", return_value=fake_tracer):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/episodes")

    assert resp.status_code == 200, resp.text
    assert fake_span.attributes.get("chronicler.episodes.filter_kind") == "none", (
        f"Expected filter_kind='none', got: {fake_span.attributes}"
    )


@pytest.mark.unit
async def test_list_episodes_span_attribute_participant_join_takes_precedence() -> None:
    """When both entity_id and participant_entity_id are supplied, filter_kind=participant_join."""
    rows = [_episode_api_row(entity_id=_ENTITY_X, participant_entity_ids=[_ENTITY_Y])]
    app = _build_api_app(rows)

    fake_span = _FakeSpan()
    fake_tracer = _FakeTracer(fake_span)

    with patch("opentelemetry.trace.get_tracer", return_value=fake_tracer):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/chronicler/episodes?entity_id={_ENTITY_X}&participant_entity_id={_ENTITY_Y}"
            )

    assert resp.status_code == 200, resp.text
    assert fake_span.attributes.get("chronicler.episodes.filter_kind") == "participant_join", (
        "participant_entity_id takes precedence: filter_kind must be 'participant_join'"
    )
