"""Forgotten-row receipts and location evidence tombstones (bu-s11n0s.7 S3).

The writer is dark (raw expiry calls it in a later slice); these tests pin its contract. The
real-SQL proof (append-only, uniqueness, grants) is in
``tests/migrations/test_location_evidence_tombstones_migration.py`` (CI-only).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from butlers.chronicler.location_forgetting import (
    ForgottenPointEvent,
    owntracks_point_forgotten,
    record_forgetting,
)
from butlers.chronicler.spatial_precision import SPATIAL_PRECISION_M, SPATIAL_SCHEME_REVISION
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.unit

_CUTOFF = datetime(2026, 10, 16, tzinfo=UTC)
_REF = "connectors.owntracks_points:owntracks:alice:1760000000:location"


def _conn(*, inserted: list[UUID]) -> MagicMock:
    """A connection whose tombstone insert returns the id only for ``inserted`` (else conflict)."""
    conn = MagicMock()
    conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
    conn.fetchval = AsyncMock(
        side_effect=lambda _sql, event_id, *_: event_id if event_id in inserted else None
    )
    conn.execute = AsyncMock()
    return conn


async def test_one_transaction_writes_the_tombstones_then_a_receipt_counting_them() -> None:
    new, already = uuid4(), uuid4()
    episode = uuid4()
    conn = _conn(inserted=[new])

    receipt = await record_forgetting(
        conn,
        policy_version=3,
        cutoff=_CUTOFF,
        episodes_coarsened=2,
        forgotten=[
            ForgottenPointEvent(new, _REF, episode_ids=(episode,)),
            ForgottenPointEvent(already, _REF + "x"),
        ],
    )

    conn.transaction.assert_called_once()
    calls = [call.args for call in conn.fetchval.await_args_list]
    assert all("ON CONFLICT DO NOTHING" in sql and "RETURNING" in sql for sql, *_ in calls)
    receipt_id = calls[0][3]
    assert [args[1:] for args in calls] == [
        (new, _REF, receipt_id, [episode]),
        (already, _REF + "x", receipt_id, []),
    ]
    receipt_sql, *values = conn.execute.await_args.args
    assert "INSERT INTO location_forgetting_receipts" in receipt_sql
    # Only the tombstone this run added counts: a re-run never doubles the receipt.
    assert values == [
        receipt_id,
        3,
        _CUTOFF,
        SPATIAL_SCHEME_REVISION,
        SPATIAL_PRECISION_M,
        1,
        2,
    ]
    assert (receipt.id, receipt.point_events_forgotten) == (receipt_id, 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"policy_version": 0},
        {"policy_version": True},
        {"cutoff": datetime(2026, 10, 16)},  # naive
        {"episodes_coarsened": -1},
    ],
)
async def test_malformed_receipts_refuse_before_any_write(kwargs: dict) -> None:
    conn = _conn(inserted=[])
    args = {"policy_version": 1, "cutoff": _CUTOFF, "episodes_coarsened": 0, **kwargs}
    with pytest.raises(ValueError):
        await record_forgetting(conn, forgotten=[], **args)
    conn.fetchval.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize("ref", ["", "point_events:abc", "connectors.other:abc"])
def test_a_tombstone_names_only_an_owntracks_source(ref: str) -> None:
    with pytest.raises(ValueError):
        ForgottenPointEvent(uuid4(), ref)


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False), (None, False)])
async def test_the_forgotten_check_is_true_only_for_a_stored_tombstone(
    value: object, expected: bool
) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=value)
    assert await owntracks_point_forgotten(conn, _REF) is expected
    sql, ref = conn.fetchval.await_args.args
    assert "location_evidence_tombstones" in sql and ref == _REF
