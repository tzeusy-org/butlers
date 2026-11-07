"""A large historical import refreshes the stored category spend bands (bu-q7vx1q.79).

The import's post-import trigger used to call ``compute_baselines``, which persists nothing, so
a six-month import left every category at ``insufficient_history`` until the next daily scan
while the result still said ``baselines_triggered``. The trigger now runs the producer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import asyncpg
import pytest

pytestmark = pytest.mark.unit

_ZONE = ZoneInfo("Asia/Singapore")
_PRODUCER = "butlers.jobs.finance_spend_baselines.refresh_category_baselines"
_ZONE_RESOLVER = "butlers.tools.finance.budgets.resolve_budget_zone"


def _csv(rows: int) -> MagicMock:
    """A blob store holding a Chase-format export with ``rows`` transactions."""
    lines = ["Transaction Date,Description,Amount,Balance"]
    for i in range(rows):
        lines.append(f"01/{(i % 28) + 1:02d}/2024,MERCHANT {i},-{i + 1}.00,500.00")
    blob_store = AsyncMock(scheme="s3")
    blob_store.get = AsyncMock(return_value="\n".join(lines).encode("utf-8"))
    return blob_store


async def _import(pool: MagicMock, rows: int) -> dict:
    from butlers.tools.finance.data_import import import_transactions

    return await import_transactions(
        pool,
        blob_store=_csv(rows),
        storage_ref="s3://bucket/finance/import.csv",
        origin_butler="finance",
    )


def _pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=False)  # no MV, no merchant mappings
    return pool


async def test_a_50_row_import_refreshes_the_bands_in_the_budget_zone_and_others_do_not():
    pool = _pool()
    with (
        patch(_PRODUCER, new=AsyncMock(return_value=[])) as producer,
        patch(_ZONE_RESOLVER, new=AsyncMock(return_value=_ZONE)),
    ):
        result = await _import(pool, 50)

    assert result["imported"] == 50
    assert result["baselines_triggered"] is True
    producer.assert_awaited_once()
    assert producer.await_args.args == (pool,)
    assert producer.await_args.kwargs["zone"] is _ZONE
    assert isinstance(producer.await_args.kwargs["now"], datetime)
    assert producer.await_args.kwargs["now"].tzinfo is UTC

    # A small import does not refresh; a failed refresh is reported as not triggered.
    with (
        patch(_PRODUCER, new=AsyncMock(return_value=[])) as small_producer,
        patch(_ZONE_RESOLVER, new=AsyncMock(return_value=_ZONE)),
    ):
        small = await _import(_pool(), 4)
    assert small["baselines_triggered"] is False
    small_producer.assert_not_awaited()

    with (
        patch(_PRODUCER, new=AsyncMock(side_effect=asyncpg.PostgresError("boom"))),
        patch(_ZONE_RESOLVER, new=AsyncMock(return_value=_ZONE)),
    ):
        failed = await _import(_pool(), 50)
    assert failed["imported"] == 50
    assert failed["baselines_triggered"] is False
