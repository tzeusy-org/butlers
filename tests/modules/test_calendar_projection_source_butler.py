from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = pytest.mark.unit


def _make_db(*, schema: str = "travel", db_name: str = "butlers", pool: MagicMock | None = None):
    return SimpleNamespace(schema=schema, db_name=db_name, pool=pool)


class TestCalendarProjectionSourceButler:
    async def test_resolve_effective_butler_name_falls_back_to_db_schema(self) -> None:
        module = CalendarModule()
        module._db = _make_db(schema="travel")
        module._butler_name = ""  # type: ignore[assignment]

        assert module._resolve_effective_butler_name(None) == "travel"

    async def test_resolve_effective_butler_name_prefers_existing_module_identity(self) -> None:
        module = CalendarModule()
        module._db = _make_db(schema="travel")
        module._butler_name = "health"  # type: ignore[assignment]

        assert module._resolve_effective_butler_name(None) == "health"

    async def test_upsert_projection_event_uses_db_schema_when_source_butler_missing(self) -> None:
        event_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": event_id})

        module = CalendarModule()
        module._db = _make_db(schema="travel", pool=pool)
        module._butler_name = None  # type: ignore[assignment]

        await module._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="evt-1",
            title="Trip check-in",
            timezone="UTC",
            starts_at=datetime(2026, 4, 16, 6, 0, tzinfo=UTC),
            ends_at=datetime(2026, 4, 16, 7, 0, tzinfo=UTC),
            status="confirmed",
            source_butler=None,
        )

        sql_args = pool.fetchrow.await_args.args
        # source_butler is the second-to-last positional argument (followed by source_session_id).
        assert sql_args[-2] == "travel"

    async def test_insert_reminder_uses_db_schema_when_module_butler_name_missing(self) -> None:
        source_id = uuid.uuid4()
        event_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                {"id": source_id},
                {
                    "id": event_id,
                    "title": "Renew passport",
                    "body": None,
                    "starts_at": datetime(2026, 4, 16, 6, 0, tzinfo=UTC),
                    "ends_at": datetime(2026, 4, 16, 6, 15, tzinfo=UTC),
                    "status": "confirmed",
                    "recurrence_rule": None,
                    "source_butler": "travel",
                    "source_session_id": None,
                },
            ]
        )
        pool.execute = AsyncMock(return_value=None)
        pool.executemany = AsyncMock(return_value=None)

        module = CalendarModule()
        module._config = module._coerce_config({"provider": "google"})
        module._db = _make_db(schema="travel", pool=pool)
        module._butler_name = None  # type: ignore[assignment]
        module._projection_tables_available_cache = True

        await module._insert_reminder_to_calendar_events(
            title="Renew passport",
            body=None,
            starts_at=datetime(2026, 4, 16, 6, 0, tzinfo=UTC),
            ends_at=datetime(2026, 4, 16, 6, 15, tzinfo=UTC),
            timezone="UTC",
            recurrence_rule=None,
            entity_ids=[],
        )

        sql_args = pool.fetchrow.await_args_list[-1].args
        # source_butler is the second-to-last positional argument (followed by session_id).
        assert sql_args[-2] == "travel"
