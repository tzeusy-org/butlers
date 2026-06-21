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
async def test_upsert_checkpoint_watermark_id_case_when_guard() -> None:
    """Data-corruption guard (migration 005): the upsert SQL must carry the
    ``CASE WHEN EXCLUDED.watermark IS NOT NULL THEN EXCLUDED.watermark_id ...`` branch
    so that advancing ``watermark`` with ``watermark_id=None`` cannot silently preserve
    the stale prior id (producing an inconsistent ``(new_ts, old_id)`` tuple), AND the
    ``watermark_id`` argument is bound as the 6th SQL parameter so a future edit cannot
    swap ids. No live-DB integration test covers this guard, so keep both assertions.
    """
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)

    wm_id = 42
    await upsert_checkpoint(pool, "src", watermark=_NOW, watermark_id=wm_id, success=True)

    assert pool.execute.called, "execute should have been called for a success upsert"
    args = pool.execute.call_args.args
    sql: str = args[0]
    # CASE WHEN guard text.
    assert "CASE" in sql, "SQL must use CASE WHEN for watermark_id"
    assert "EXCLUDED.watermark IS NOT NULL" in sql, (
        "SQL must guard on whether a new watermark is being written"
    )
    assert "EXCLUDED.watermark_id" in sql, "SQL must reference EXCLUDED.watermark_id in the CASE"
    # Parameter ordering: args[1..6] = source_name, subsource, watermark, now,
    # rows_projected, watermark_id.
    assert args[6] == wm_id, (
        f"watermark_id ({wm_id}) must be the 6th SQL parameter; got {args[6]!r}"
    )
