"""Tests for entity_id resolution in the calendar adapter and backfill script (bu-f4755).

Covers ``CalendarCompletedAdapter._resolve_schema_entity_id``:
- Happy path: schema → account_email → entity_id resolved.
- No user-lane calendar source → returns None.
- No google_accounts row → returns None.
- entity_id IS NULL on google_accounts row → returns None.
- PostgresError on any query → returns None (graceful degrade).
- entity_id returned as str is coerced to UUID.

Also covers the adapter's ``project()`` method to verify entity_id flows
through to the Episode upsert correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
from butlers.testing.fake_pool import AsyncCtx, fake_pool
from butlers.testing.records import row

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ENTITY_ID = uuid4()
_ACCOUNT_EMAIL = "owner@example.com"
_SCHEMA = "test_butler"


# ---------------------------------------------------------------------------
# Tests for CalendarCompletedAdapter._resolve_schema_entity_id
# (This is the pure resolution function used by both the adapter at
# projection time and the backfill script for historical episodes.)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_resolve_schema_entity_id_happy_path() -> None:
    """Happy path: account_email found → entity_id resolved."""
    pool = fake_pool(
        fetchrow=AsyncMock(
            side_effect=[row(account_email=_ACCOUNT_EMAIL), row(entity_id=_ENTITY_ID)]
        )
    )
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result == _ENTITY_ID


@pytest.mark.unit
async def test_resolve_schema_entity_id_no_calendar_source() -> None:
    """No user-lane calendar source with account_email → None."""
    pool = fake_pool(fetchrow=AsyncMock(side_effect=[None, None]))
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result is None


@pytest.mark.unit
async def test_resolve_schema_entity_id_no_google_account_row() -> None:
    """account_email resolved but not in google_accounts → None."""
    pool = fake_pool(fetchrow=AsyncMock(side_effect=[row(account_email=_ACCOUNT_EMAIL), None]))
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result is None


@pytest.mark.unit
async def test_resolve_schema_entity_id_null_on_account_row() -> None:
    """google_accounts row exists but entity_id IS NULL → None."""
    pool = fake_pool(
        fetchrow=AsyncMock(side_effect=[row(account_email=_ACCOUNT_EMAIL), row(entity_id=None)])
    )
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result is None


@pytest.mark.unit
async def test_resolve_schema_entity_id_postgres_error_returns_none() -> None:
    """PostgresError on any query → returns None without raising."""
    pool = fake_pool(fetchrow=AsyncMock(side_effect=asyncpg.PostgresError("test error")))
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result is None


@pytest.mark.unit
async def test_resolve_schema_entity_id_string_uuid_is_coerced() -> None:
    """entity_id returned as str (not UUID) is coerced to UUID."""
    pool = fake_pool(
        fetchrow=AsyncMock(
            side_effect=[row(account_email=_ACCOUNT_EMAIL), row(entity_id=str(_ENTITY_ID))]
        )
    )
    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    result = await adapter._resolve_schema_entity_id(pool, _SCHEMA)
    assert result == _ENTITY_ID
    assert isinstance(result, UUID)


@pytest.mark.unit
async def test_adapter_project_writes_resolved_entity_to_episode_entities() -> None:
    """When entity_id is resolved, it is written to episode_entities as the owner.

    The derived episodes.entity_id column was dropped (bu-cfsgy); the resolved
    owner now flows only into the episode_entities join table.
    """
    from unittest.mock import patch

    from butlers.chronicler.models import Episode

    entity_id = uuid4()

    # Build a minimal row dict.
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

    class _Row(dict):
        def __getattr__(self, name: str) -> object:
            try:
                return self[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

    row = _Row(
        id=uuid4(),
        event_id=uuid4(),
        source_id=uuid4(),
        origin_instance_ref="evt:test:2026-05-01T12:00:00Z",
        starts_at=now - timedelta(hours=1),
        ends_at=now,
        status="confirmed",
        timezone="UTC",
        metadata={},
        updated_at=now,
        event_title="Team standup",
        event_description=None,
        event_location=None,
    )

    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    captured: list[Episode] = []
    episode_id = uuid4()
    owner_calls: list[tuple] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        stored = Episode(
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
        )
        captured.append(stored)
        return stored

    async def _fake_upsert_entities(conn, ep_id, *, owner_id, participant_ids):
        owner_calls.append((ep_id, owner_id))

    # Mock pool returns one row and entity_id
    def _make_project_pool() -> AsyncMock:
        conn = AsyncMock()
        conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
        conn.fetchrow = AsyncMock(return_value=None)
        p = AsyncMock()
        p.acquire = MagicMock(side_effect=lambda: AsyncCtx(conn))
        return p

    with (
        patch.object(
            adapter,
            "_fetch_instances",
            new=AsyncMock(return_value=[row]),
        ),
        patch.object(
            adapter,
            "_resolve_schema_entity_id",
            new=AsyncMock(return_value=entity_id),
        ),
        patch.object(
            CalendarCompletedAdapter,
            "_upsert_episode_entities",
            autospec=True,
            side_effect=_fake_upsert_entities,
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_make_project_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    assert len(captured) == 1
    assert owner_calls == [(episode_id, entity_id)], (
        "Resolved entity must be written to episode_entities as the owner"
    )


@pytest.mark.unit
async def test_adapter_project_owner_none_when_unresolved() -> None:
    """When entity_id cannot be resolved, the episode_entities owner_id is None."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from butlers.chronicler.models import Episode

    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

    class _Row(dict):
        def __getattr__(self, name: str) -> object:
            try:
                return self[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

    row = _Row(
        id=uuid4(),
        event_id=uuid4(),
        source_id=uuid4(),
        origin_instance_ref="evt:test2:2026-05-01T12:00:00Z",
        starts_at=now - timedelta(hours=1),
        ends_at=now,
        status="confirmed",
        timezone="UTC",
        metadata={},
        updated_at=now,
        event_title="Solo block",
        event_description=None,
        event_location=None,
    )

    adapter = CalendarCompletedAdapter(butler_schemas=(_SCHEMA,))
    captured: list[Episode] = []
    owner_calls: list[tuple] = []

    async def _fake_upsert(_conn: object, episode: Episode) -> Episode:
        captured.append(episode)
        return episode

    async def _fake_upsert_entities(conn, ep_id, *, owner_id, participant_ids):
        owner_calls.append((ep_id, owner_id))

    def _make_project_pool() -> AsyncMock:
        conn = AsyncMock()
        conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
        conn.fetchrow = AsyncMock(return_value=None)
        p = AsyncMock()
        p.acquire = MagicMock(side_effect=lambda: AsyncCtx(conn))
        return p

    with (
        patch.object(adapter, "_fetch_instances", new=AsyncMock(return_value=[row])),
        patch.object(adapter, "_resolve_schema_entity_id", new=AsyncMock(return_value=None)),
        patch.object(
            CalendarCompletedAdapter,
            "_upsert_episode_entities",
            autospec=True,
            side_effect=_fake_upsert_entities,
        ),
        patch(
            "butlers.chronicler.adapters.calendar.upsert_episode",
            side_effect=_fake_upsert,
        ),
    ):
        result = await adapter.project(
            MagicMock(),
            chronicler_pool=_make_project_pool(),
            since=None,
        )

    assert result.rows_projected == 1
    assert len(owner_calls) == 1
    assert owner_calls[0][1] is None
