"""Real-Postgres: wake provenance is written with the hold and the flush fences protocol rows.

Bead bu-kqnum.3.6.1 (core_272). Drives the real ``insert_deferred_notification`` and the real
ordinary flush against a migrated schema.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.scheduler import _tick_deferred_notification_pass
from butlers.core.temporal.delivery_db import (
    WakeHoldProvenance,
    envelope_digest,
    insert_deferred_notification,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture
async def pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        pool_schema="switchboard",
    )
    try:
        yield p
    finally:
        await p.close()


def _envelope(text: str) -> dict:
    return {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {"intent": "send", "channel": "telegram", "message": text, "recipient": text},
    }


def _provenance(envelope: dict) -> WakeHoldProvenance:
    return WakeHoldProvenance(
        policy_window_key="2026-10-18T22:00:00+00:00/2026-10-19T07:00:00+00:00",
        timezone="UTC",
        target={"provider": "telegram", "chat_id": "42", "thread_id": None},
        envelope_digest=envelope_digest(envelope),
        original_deliver_at=_NOW - timedelta(hours=1),
    )


async def _park(pool, text: str, *, wake: bool, age: timedelta = timedelta(hours=1)) -> str:
    envelope = _envelope(text)
    return await insert_deferred_notification(
        pool,
        butler_name="health",
        channel="telegram",
        message=text,
        priority="medium",
        envelope=envelope,
        deliver_at=_NOW - age,
        wake_provenance=_provenance(envelope) if wake else None,
    )


async def test_provenance_lands_in_the_same_row_and_other_holds_carry_none(pool) -> None:
    held = await _park(pool, "wake", wake=True)
    plain = await _park(pool, "plain", wake=False)

    rows = {
        str(r["id"]): r
        for r in await pool.fetch(
            "SELECT id, status, wake_hold_kind, wake_admission_sequence, wake_target,"
            " wake_envelope_digest FROM deferred_notifications"
        )
    }
    assert rows[held]["status"] == "pending"
    assert rows[held]["wake_hold_kind"] == "owner_attention_quiet_hours"
    assert rows[held]["wake_admission_sequence"] is not None
    assert rows[held]["wake_target"]["chat_id"] == "42"
    assert rows[held]["wake_envelope_digest"] == envelope_digest(_envelope("wake"))
    assert rows[plain]["wake_hold_kind"] is None
    assert rows[plain]["wake_admission_sequence"] is None


async def test_admission_sequence_increases_with_each_hold(pool) -> None:
    await _park(pool, "first", wake=True)
    await _park(pool, "second", wake=True)

    by_message = {
        r["message"]: r["wake_admission_sequence"]
        for r in await pool.fetch(
            "SELECT message, wake_admission_sequence FROM deferred_notifications"
        )
    }
    assert by_message["second"] > by_message["first"]


async def test_the_flush_leaves_protocol_rows_and_handles_plain_and_legacy_rows(pool) -> None:
    due = await _park(pool, "due", wake=True)
    legacy_stale = await _park(pool, "legacy-stale", wake=False, age=timedelta(hours=30))
    prepared = await _park(pool, "prepared", wake=True)
    committed = await _park(pool, "committed", wake=True, age=timedelta(hours=30))
    await pool.execute(
        "UPDATE deferred_notifications SET status = 'release_prepared' WHERE id = $1::uuid",
        prepared,
    )
    await pool.execute(
        "UPDATE deferred_notifications SET status = 'release_committed' WHERE id = $1::uuid",
        committed,
    )
    notify_fn = AsyncMock()

    with patch("butlers.core.attention_ledger.record_attention_event", AsyncMock()):
        delivered = await _tick_deferred_notification_pass(pool, _NOW, notify_fn=notify_fn)

    status = {
        str(r["id"]): r["status"]
        for r in await pool.fetch("SELECT id, status FROM deferred_notifications")
    }
    assert delivered == 1
    assert status[due] == "delivered"
    assert status[legacy_stale] == "expired"
    assert status[prepared] == "release_prepared"
    assert status[committed] == "release_committed"
