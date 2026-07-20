"""Source-ledger hygiene for the calendar module (bu-ssp91).

Covers the two write-side halves of origin-identity dedup + ledger hygiene:

- provider sync prunes older instances left behind by a time-drifted re-sync so
  the ledger converges to one instance per provider event, and
- the ``__invalid_check__`` probe/sentinel calendar source is never registered
  and any residual row is purged once on startup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.calendar import (
    _INVALID_PROBE_CALENDAR_IDS,
    _OBSOLETE_INTERNAL_SOURCE_KEYS,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    SOURCE_KIND_PROVIDER,
    CalendarEvent,
    CalendarModule,
)

pytestmark = pytest.mark.unit


def _make_db(*, schema: str = "assistant", pool: MagicMock | None = None):
    return SimpleNamespace(schema=schema, db_name="butlers", pool=pool)


class TestPruneSupersededProviderInstances:
    async def test_deletes_other_instances_of_event(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        module = CalendarModule()
        module._db = _make_db(pool=pool)

        event_id = uuid.uuid4()
        keep_ref = f"{event_id}:2026-02-22T14:00:00+00:00"
        await module._prune_superseded_provider_instances(
            event_id=event_id,
            keep_origin_instance_ref=keep_ref,
        )

        pool.execute.assert_awaited_once()
        sql, *args = pool.execute.await_args.args
        normalized = " ".join(sql.split())
        assert "DELETE FROM calendar_event_instances" in normalized
        assert "origin_instance_ref IS DISTINCT FROM" in normalized
        assert args == [event_id, keep_ref]

    async def test_fail_open_on_pool_error(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("boom"))
        module = CalendarModule()
        module._db = _make_db(pool=pool)

        # Must not raise — the sync loop stays alive.
        await module._prune_superseded_provider_instances(
            event_id=uuid.uuid4(),
            keep_origin_instance_ref="ref",
        )

    async def test_noop_without_pool(self) -> None:
        module = CalendarModule()
        module._db = _make_db(pool=None)
        await module._prune_superseded_provider_instances(
            event_id=uuid.uuid4(),
            keep_origin_instance_ref="ref",
        )

    async def test_project_provider_changes_prunes_with_written_ref(self) -> None:
        module = CalendarModule()
        module._db = _make_db()
        module._butler_name = "assistant"

        event_db_id = uuid.uuid4()
        module._upsert_projection_event = AsyncMock(return_value=event_db_id)
        module._upsert_projection_instance = AsyncMock(return_value=uuid.uuid4())
        module._prune_superseded_provider_instances = AsyncMock()

        start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
        event = CalendarEvent(
            event_id="lou",
            title="Lou Shang farewell",
            start_at=start,
            end_at=start,
            timezone="UTC",
        )

        await module._project_provider_changes(
            source_id=uuid.uuid4(),
            provider_name="google",
            calendar_id="primary",
            updated_events=[event],
            cancelled_ids=[],
        )

        expected_ref = f"lou:{start.isoformat()}"
        module._upsert_projection_instance.assert_awaited_once()
        assert (
            module._upsert_projection_instance.await_args.kwargs["origin_instance_ref"]
            == expected_ref
        )
        module._prune_superseded_provider_instances.assert_awaited_once_with(
            event_id=event_db_id,
            keep_origin_instance_ref=expected_ref,
        )


class TestInvalidProbeSourceHygiene:
    async def test_ensure_source_refuses_probe_calendar_id(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        result = await module._ensure_calendar_source(
            source_key="provider:google:__invalid_check__",
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            provider="google",
            calendar_id="__invalid_check__",
        )

        assert result is None
        pool.fetchrow.assert_not_awaited()  # never written

    async def test_ensure_source_allows_real_calendar_id(self) -> None:
        new_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": new_id})
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        result = await module._ensure_calendar_source(
            source_key="provider:google:primary",
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            provider="google",
            calendar_id="primary",
        )

        assert result == new_id
        pool.fetchrow.assert_awaited_once()

    async def test_purge_deletes_probe_sources(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 1")
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        await module._purge_invalid_probe_sources()

        pool.execute.assert_awaited_once()
        sql, *args = pool.execute.await_args.args
        normalized = " ".join(sql.split())
        assert "DELETE FROM calendar_sources" in normalized
        assert "calendar_id = ANY" in normalized
        assert args == [list(_INVALID_PROBE_CALENDAR_IDS)]

    async def test_purge_noop_when_projection_unavailable(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = False

        await module._purge_invalid_probe_sources()

        pool.execute.assert_not_awaited()

    def test_sentinel_set_contains_invalid_check(self) -> None:
        assert "__invalid_check__" in _INVALID_PROBE_CALENDAR_IDS


class TestObsoleteInternalSourceHygiene:
    def test_obsolete_source_key_set_is_exactly_bounded(self) -> None:
        assert _OBSOLETE_INTERNAL_SOURCE_KEYS == (
            "internal_scheduler:butler",
            "internal_scheduler:butlers",
            "internal_reminders:butlers",
        )

    async def test_purge_deletes_only_the_exact_obsolete_internal_source_keys(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="DELETE 3")
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        await module._purge_obsolete_internal_sources()

        pool.execute.assert_awaited_once()
        sql, *args = pool.execute.await_args.args
        normalized = " ".join(sql.split())
        assert "DELETE FROM calendar_sources" in normalized
        assert "source_key = ANY" in normalized
        assert args == [list(_OBSOLETE_INTERNAL_SOURCE_KEYS)]
        assert "LIKE" not in normalized

    async def test_purge_noops_when_projection_tables_are_unavailable(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = False

        await module._purge_obsolete_internal_sources()

        pool.execute.assert_not_awaited()

    @pytest.mark.parametrize(
        "source_kind",
        [SOURCE_KIND_INTERNAL_SCHEDULER, SOURCE_KIND_INTERNAL_REMINDERS],
    )
    async def test_invalid_roster_name_cannot_register_internal_source(
        self, source_kind: str
    ) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        result = await module._ensure_calendar_source(
            source_key=f"{source_kind}:ghost",
            source_kind=source_kind,
            lane="butler",
            butler_name="ghost",
        )

        assert result is None
        pool.fetchrow.assert_not_awaited()

    async def test_valid_roster_name_still_uses_normal_internal_source_upsert(self) -> None:
        source_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": source_id})
        module = CalendarModule()
        module._db = _make_db(pool=pool)
        module._projection_tables_available_cache = True

        result = await module._ensure_calendar_source(
            source_key="internal_scheduler:general",
            source_kind=SOURCE_KIND_INTERNAL_SCHEDULER,
            lane="butler",
            butler_name="general",
        )

        assert result == source_id
        pool.fetchrow.assert_awaited_once()
