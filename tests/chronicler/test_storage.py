"""Unit tests for chronicler storage functions.

Focuses on the SQL semantics of ``_upsert_checkpoint_row`` — specifically the
CASE WHEN guard introduced in migration 005 to prevent stale ``watermark_id``
values persisting when a legacy adapter advances the timestamp watermark without
providing a new id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.storage import upsert_checkpoint

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_upsert_checkpoint_sql_uses_case_when_for_watermark_id() -> None:
    """``watermark_id`` CASE WHEN guard must appear in the upsert SQL.

    Without the guard, advancing ``watermark`` while passing ``watermark_id=None``
    would silently preserve the stale id from the prior checkpoint row, producing
    an inconsistent tuple ``(new_ts, old_id)`` used by tuple-aware adapters.

    This test verifies the SQL text carries the CASE WHEN ... THEN EXCLUDED.watermark_id
    branch so the intent is explicit and regressions are caught at the source.
    """
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)

    await upsert_checkpoint(pool, "test_source", watermark=_NOW, watermark_id=None, success=True)

    assert pool.execute.called, "execute should have been called for a success upsert"
    sql: str = pool.execute.call_args.args[0]

    # The CASE WHEN guard must be present.
    assert "CASE" in sql, "SQL must use CASE WHEN for watermark_id"
    assert "EXCLUDED.watermark IS NOT NULL" in sql, (
        "SQL must guard on whether a new watermark is being written"
    )
    assert "EXCLUDED.watermark_id" in sql, "SQL must reference EXCLUDED.watermark_id in the CASE"


@pytest.mark.asyncio
async def test_upsert_checkpoint_passes_watermark_id_arg_as_sixth_param() -> None:
    """``watermark_id`` is bound as the sixth SQL parameter ($6).

    Verifies the parameter ordering so a future edit does not silently swap ids.
    """
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)

    wm_id = 42
    await upsert_checkpoint(pool, "src", watermark=_NOW, watermark_id=wm_id, success=True)

    positional_args = pool.execute.call_args.args
    # args[0] is SQL, args[1..6] are parameters: source_name, subsource, watermark, now, rows_projected, watermark_id
    assert positional_args[6] == wm_id, (
        f"watermark_id ({wm_id}) must be the 6th SQL parameter; got {positional_args[6]!r}"
    )
