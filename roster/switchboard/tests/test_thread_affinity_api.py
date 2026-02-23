"""Unit tests for thread-affinity API endpoints.

Tests GET/PATCH settings and override CRUD using mocked DB pool.
No Docker required — all DB calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# Butler DB key used by the switchboard router's _pool() helper
_BUTLER_DB = "switchboard"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings_row(
    *,
    enabled: bool = True,
    ttl_days: int = 30,
    overrides: dict | None = None,
    updated_at: str = "2026-02-23T00:00:00+00:00",
) -> MagicMock:
    data = {
        "thread_affinity_enabled": enabled,
        "thread_affinity_ttl_days": ttl_days,
        "thread_overrides": overrides if overrides is not None else {},
        "updated_at": updated_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.__bool__ = lambda self: True
    return row


def _make_db(pool: AsyncMock) -> MagicMock:
    """Build a mock DatabaseManager that returns the given pool for any butler name."""
    db = MagicMock()
    db.pool = MagicMock(return_value=pool)
    return db


def _make_pool(
    *,
    fetchrow_return=None,
    execute_return=None,
) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


# ---------------------------------------------------------------------------
# GET /thread-affinity/settings
# ---------------------------------------------------------------------------


class TestGetThreadAffinitySettings:
    async def test_returns_default_settings(self) -> None:
        pool = _make_pool(fetchrow_return=_fake_settings_row())
        db = _make_db(pool)

        from roster.switchboard.api.router import get_thread_affinity_settings

        result = await get_thread_affinity_settings(db=db)
        assert result.enabled is True
        assert result.ttl_days == 30
        assert result.thread_overrides == {}

    async def test_returns_settings_with_overrides(self) -> None:
        pool = _make_pool(
            fetchrow_return=_fake_settings_row(
                enabled=False,
                ttl_days=14,
                overrides={"tid-1": "force:finance", "tid-2": "disabled"},
            )
        )
        db = _make_db(pool)

        from roster.switchboard.api.router import get_thread_affinity_settings

        result = await get_thread_affinity_settings(db=db)
        assert result.enabled is False
        assert result.ttl_days == 14
        assert result.thread_overrides == {"tid-1": "force:finance", "tid-2": "disabled"}

    async def test_raises_404_when_row_missing(self) -> None:
        from fastapi import HTTPException

        pool = _make_pool(fetchrow_return=None)
        db = _make_db(pool)

        from roster.switchboard.api.router import get_thread_affinity_settings

        with pytest.raises(HTTPException) as exc_info:
            await get_thread_affinity_settings(db=db)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /thread-affinity/settings
# ---------------------------------------------------------------------------


class TestUpdateThreadAffinitySettings:
    async def test_update_enabled_field(self) -> None:
        pool = _make_pool(fetchrow_return=_fake_settings_row(enabled=False))
        db = _make_db(pool)

        from roster.switchboard.api.models import ThreadAffinitySettingsUpdate
        from roster.switchboard.api.router import update_thread_affinity_settings

        body = ThreadAffinitySettingsUpdate(enabled=False)
        result = await update_thread_affinity_settings(body=body, db=db)
        assert result.enabled is False
        pool.execute.assert_called_once()

    async def test_update_ttl_days(self) -> None:
        pool = _make_pool(fetchrow_return=_fake_settings_row(ttl_days=14))
        db = _make_db(pool)

        from roster.switchboard.api.models import ThreadAffinitySettingsUpdate
        from roster.switchboard.api.router import update_thread_affinity_settings

        body = ThreadAffinitySettingsUpdate(ttl_days=14)
        result = await update_thread_affinity_settings(body=body, db=db)
        assert result.ttl_days == 14

    async def test_no_fields_raises_422(self) -> None:
        from fastapi import HTTPException

        from roster.switchboard.api.models import ThreadAffinitySettingsUpdate
        from roster.switchboard.api.router import update_thread_affinity_settings

        pool = _make_pool()
        db = _make_db(pool)

        body = ThreadAffinitySettingsUpdate()  # No fields
        with pytest.raises(HTTPException) as exc_info:
            await update_thread_affinity_settings(body=body, db=db)

        assert exc_info.value.status_code == 422

    async def test_zero_ttl_days_raises_422(self) -> None:
        from fastapi import HTTPException

        from roster.switchboard.api.models import ThreadAffinitySettingsUpdate
        from roster.switchboard.api.router import update_thread_affinity_settings

        pool = _make_pool()
        db = _make_db(pool)

        body = ThreadAffinitySettingsUpdate(ttl_days=0)
        with pytest.raises(HTTPException) as exc_info:
            await update_thread_affinity_settings(body=body, db=db)

        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# GET /thread-affinity/overrides
# ---------------------------------------------------------------------------


class TestListThreadAffinityOverrides:
    async def test_returns_empty_list_when_no_overrides(self) -> None:
        pool = _make_pool(fetchrow_return=_fake_settings_row(overrides={}))
        db = _make_db(pool)

        from roster.switchboard.api.router import list_thread_affinity_overrides

        result = await list_thread_affinity_overrides(db=db)
        assert result == []

    async def test_returns_override_entries(self) -> None:
        pool = _make_pool(
            fetchrow_return=_fake_settings_row(
                overrides={"thread-001": "force:health", "thread-002": "disabled"}
            )
        )
        db = _make_db(pool)

        from roster.switchboard.api.router import list_thread_affinity_overrides

        result = await list_thread_affinity_overrides(db=db)
        assert len(result) == 2
        thread_ids = {entry.thread_id for entry in result}
        assert "thread-001" in thread_ids
        assert "thread-002" in thread_ids

    async def test_returns_empty_list_when_row_missing(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        db = _make_db(pool)

        from roster.switchboard.api.router import list_thread_affinity_overrides

        result = await list_thread_affinity_overrides(db=db)
        assert result == []


# ---------------------------------------------------------------------------
# PUT /thread-affinity/overrides/:thread_id
# ---------------------------------------------------------------------------


class TestUpsertThreadAffinityOverride:
    async def test_upsert_disabled_override(self) -> None:
        pool = _make_pool()
        db = _make_db(pool)

        from roster.switchboard.api.models import ThreadOverrideUpsert
        from roster.switchboard.api.router import upsert_thread_affinity_override

        body = ThreadOverrideUpsert(mode="disabled")
        result = await upsert_thread_affinity_override(thread_id="thread-001", body=body, db=db)

        assert result.thread_id == "thread-001"
        assert result.mode == "disabled"
        assert pool.execute.call_count == 2  # INSERT (ensure singleton) + UPDATE

    async def test_upsert_force_override(self) -> None:
        pool = _make_pool()
        db = _make_db(pool)

        from roster.switchboard.api.models import ThreadOverrideUpsert
        from roster.switchboard.api.router import upsert_thread_affinity_override

        body = ThreadOverrideUpsert(mode="force:finance")
        result = await upsert_thread_affinity_override(
            thread_id="<thread-abc@example.com>", body=body, db=db
        )

        assert result.thread_id == "<thread-abc@example.com>"
        assert result.mode == "force:finance"

    async def test_whitespace_thread_id_stripped(self) -> None:
        pool = _make_pool()
        db = _make_db(pool)

        from roster.switchboard.api.models import ThreadOverrideUpsert
        from roster.switchboard.api.router import upsert_thread_affinity_override

        body = ThreadOverrideUpsert(mode="disabled")
        result = await upsert_thread_affinity_override(thread_id="  thread-001  ", body=body, db=db)

        assert result.thread_id == "thread-001"

    async def test_raises_422_for_empty_thread_id(self) -> None:
        from fastapi import HTTPException

        from roster.switchboard.api.models import ThreadOverrideUpsert
        from roster.switchboard.api.router import upsert_thread_affinity_override

        pool = _make_pool()
        db = _make_db(pool)

        body = ThreadOverrideUpsert(mode="disabled")
        with pytest.raises(HTTPException) as exc_info:
            await upsert_thread_affinity_override(thread_id="", body=body, db=db)

        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /thread-affinity/overrides/:thread_id
# ---------------------------------------------------------------------------


class TestDeleteThreadAffinityOverride:
    async def test_delete_removes_override(self) -> None:
        pool = _make_pool()
        db = _make_db(pool)

        from roster.switchboard.api.router import delete_thread_affinity_override

        # Should not raise
        await delete_thread_affinity_override(thread_id="thread-001", db=db)
        pool.execute.assert_called_once()

    async def test_delete_raises_422_for_empty_thread_id(self) -> None:
        from fastapi import HTTPException

        from roster.switchboard.api.router import delete_thread_affinity_override

        pool = _make_pool()
        db = _make_db(pool)

        with pytest.raises(HTTPException) as exc_info:
            await delete_thread_affinity_override(thread_id="", db=db)

        assert exc_info.value.status_code == 422

    async def test_delete_raises_422_for_whitespace_thread_id(self) -> None:
        from fastapi import HTTPException

        from roster.switchboard.api.router import delete_thread_affinity_override

        pool = _make_pool()
        db = _make_db(pool)

        with pytest.raises(HTTPException) as exc_info:
            await delete_thread_affinity_override(thread_id="   ", db=db)

        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestThreadOverrideUpsertValidation:
    def test_disabled_mode_valid(self) -> None:
        from roster.switchboard.api.models import ThreadOverrideUpsert

        m = ThreadOverrideUpsert(mode="disabled")
        assert m.mode == "disabled"

    def test_force_mode_with_butler_valid(self) -> None:
        from roster.switchboard.api.models import ThreadOverrideUpsert

        m = ThreadOverrideUpsert(mode="force:finance")
        assert m.mode == "force:finance"

    def test_force_mode_without_butler_invalid(self) -> None:
        from roster.switchboard.api.models import ThreadOverrideUpsert

        with pytest.raises(Exception):
            ThreadOverrideUpsert(mode="force:")

    def test_unknown_mode_invalid(self) -> None:
        from roster.switchboard.api.models import ThreadOverrideUpsert

        with pytest.raises(Exception):
            ThreadOverrideUpsert(mode="allow")

    def test_empty_mode_invalid(self) -> None:
        from roster.switchboard.api.models import ThreadOverrideUpsert

        with pytest.raises(Exception):
            ThreadOverrideUpsert(mode="")
