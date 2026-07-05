"""Unit tests for the owner-outbound-message point-event recording helper.

Covers (bu-whhll.8):
- Insert path: correct SQL target, params, ON CONFLICT dedup handling.
- Idempotency key is a one-way hash — the raw dedup_material never appears
  in the key, and the same (provider, dedup_material) always hashes the same.
- Different providers with the same dedup_material never collide.
- Fail-soft behavior: None pool and DB errors both return False, never raise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.owner_outbound_events import (
    _hash_dedup_material,
    record_owner_outbound_point,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)


def test_hash_dedup_material_is_deterministic() -> None:
    a = _hash_dedup_material("telegram", "chat123:456")
    b = _hash_dedup_material("telegram", "chat123:456")
    assert a == b


def test_hash_dedup_material_never_contains_raw_material() -> None:
    digest = _hash_dedup_material("telegram", "chat123:456")
    assert "chat123" not in digest
    assert "456" not in digest


def test_hash_dedup_material_differs_across_providers() -> None:
    tg = _hash_dedup_material("telegram", "chat123:456")
    wa = _hash_dedup_material("whatsapp", "chat123:456")
    assert tg != wa


@pytest.mark.asyncio
async def test_record_owner_outbound_point_inserts_metadata_only() -> None:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value="some-uuid")

    inserted = await record_owner_outbound_point(
        pool,
        channel="telegram_user_client",
        provider="telegram",
        endpoint_identity="telegram:user:999",
        occurred_at=_NOW,
        dedup_material="chat123:456",
    )

    assert inserted is True
    pool.fetchval.assert_awaited_once()
    call_args = pool.fetchval.call_args
    sql = call_args.args[0]
    assert "connectors.owner_outbound_events" in sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    params = call_args.args[1:]
    # (idempotency_key, channel, endpoint_identity, occurred_at) — no content,
    # no counterpart/chat identifier in cleartext.
    assert len(params) == 4
    idempotency_key, channel, endpoint_identity, occurred_at = params
    assert "chat123" not in idempotency_key
    assert channel == "telegram_user_client"
    assert endpoint_identity == "telegram:user:999"
    assert occurred_at == _NOW


@pytest.mark.asyncio
async def test_record_owner_outbound_point_dedup_returns_false() -> None:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)  # ON CONFLICT DO NOTHING -> no row

    inserted = await record_owner_outbound_point(
        pool,
        channel="whatsapp_user_client",
        provider="whatsapp",
        endpoint_identity="whatsapp:+12025551234",
        occurred_at=_NOW,
        dedup_material="chat-jid:msg-1",
    )

    assert inserted is False


@pytest.mark.asyncio
async def test_record_owner_outbound_point_none_pool_is_noop() -> None:
    inserted = await record_owner_outbound_point(
        None,
        channel="telegram_user_client",
        provider="telegram",
        endpoint_identity="telegram:user:999",
        occurred_at=_NOW,
        dedup_material="chat123:456",
    )
    assert inserted is False


@pytest.mark.asyncio
async def test_record_owner_outbound_point_swallows_db_errors() -> None:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("connection lost"))

    inserted = await record_owner_outbound_point(
        pool,
        channel="telegram_user_client",
        provider="telegram",
        endpoint_identity="telegram:user:999",
        occurred_at=_NOW,
        dedup_material="chat123:456",
    )
    assert inserted is False
