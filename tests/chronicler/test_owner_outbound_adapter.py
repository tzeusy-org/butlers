"""Tests for the owner-outbound-message Chronicler projection adapter (bu-whhll.8).

Covers:
- Per-point event projection (one owner_outbound_message per evidence row).
- Payload privacy: only channel reaches the point event, never content or
  any counterpart identity.
- No episode is ever created (point events only — never counted alone).
- Missing evidence surface graceful degradation.
- Watermark advances on ``occurred_at``.
- Source-scan guardrail: no LLM imports in adapters/owner_outbound.py.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.owner_outbound import (
    EVENT_TYPE_OWNER_OUTBOUND,
    SOURCE_NAME,
    OwnerOutboundMessageAdapter,
)
from butlers.chronicler.models import Layer, Precision, Privacy

_NOW = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)


def _make_row(
    row_id: str = "some-uuid",
    channel: str = "telegram_user_client",
    occurred_at: datetime = _NOW,
) -> dict:
    return {"id": row_id, "channel": channel, "occurred_at": occurred_at}


def _make_mock_row(r: dict) -> MagicMock:
    return MagicMock(**r, **{"__getitem__": lambda s, k, _r=r: _r[k]})


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _pool_returning(*rows: dict) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)  # table-exists check
    conn.fetch = AsyncMock(return_value=[_make_mock_row(r) for r in rows])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _pool_table_missing() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _chronicler_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# Source-scan guardrail
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_owner_outbound_adapter() -> None:
    import butlers.chronicler.adapters.owner_outbound as mod

    source_path = mod.__file__
    assert source_path is not None

    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for prefix in forbidden_prefixes:
                    assert not node.module.startswith(prefix)


# ---------------------------------------------------------------------------
# Per-point event projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_single_row_produces_one_point_event() -> None:
    row = _make_row()
    adapter = OwnerOutboundMessageAdapter()

    upserted_events = []

    async def _fake_upsert_event(conn: object, event: object) -> object:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.owner_outbound.upsert_point_event",
        side_effect=_fake_upsert_event,
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 1
    assert result.point_events == 1
    assert len(upserted_events) == 1
    ev = upserted_events[0]
    assert ev.source_name == SOURCE_NAME
    assert ev.event_type == EVENT_TYPE_OWNER_OUTBOUND
    assert ev.occurred_at == _NOW
    assert ev.precision == Precision.EXACT
    assert ev.privacy == Privacy.NORMAL
    assert ev.layer == Layer.EVIDENCE


@pytest.mark.asyncio
async def test_point_event_payload_carries_channel_only() -> None:
    """Privacy: payload must never carry content or counterpart identity."""
    row = _make_row(channel="whatsapp_user_client")
    adapter = OwnerOutboundMessageAdapter()
    upserted_events = []

    async def _fake_upsert_event(conn: object, event: object) -> object:
        upserted_events.append(event)
        return event

    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.owner_outbound.upsert_point_event",
        side_effect=_fake_upsert_event,
    ):
        await adapter.project(pool, chronicler_pool=cp, since=None)

    ev = upserted_events[0]
    assert ev.payload == {"channel": "whatsapp_user_client"}


@pytest.mark.asyncio
async def test_no_episode_ever_created() -> None:
    """Point events only — an owner-outbound message must never inflate lane time alone."""
    row = _make_row()
    adapter = OwnerOutboundMessageAdapter()
    pool = _pool_returning(row)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.owner_outbound.upsert_point_event",
        new=AsyncMock(return_value=MagicMock()),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.episodes_opened == 0
    assert result.episodes_closed == 0


@pytest.mark.asyncio
async def test_multiple_rows_each_produce_a_point_event_and_watermark_advances() -> None:
    row1 = _make_row(row_id="a", occurred_at=_NOW)
    later = datetime(2026, 7, 5, 11, 0, 0, tzinfo=UTC)
    row2 = _make_row(row_id="b", occurred_at=later)
    adapter = OwnerOutboundMessageAdapter()
    pool = _pool_returning(row1, row2)
    cp = _chronicler_pool()

    with patch(
        "butlers.chronicler.adapters.owner_outbound.upsert_point_event",
        new=AsyncMock(return_value=MagicMock()),
    ):
        result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.rows_projected == 2
    assert result.point_events == 2
    assert result.watermark == later


@pytest.mark.asyncio
async def test_missing_evidence_table_degrades_gracefully() -> None:
    adapter = OwnerOutboundMessageAdapter()
    pool = _pool_table_missing()
    cp = _chronicler_pool()

    result = await adapter.project(pool, chronicler_pool=cp, since=None)

    assert result.skipped is True
    assert "owner_outbound_events" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_no_new_rows_keeps_prior_watermark() -> None:
    adapter = OwnerOutboundMessageAdapter()
    pool = _pool_returning()  # empty
    cp = _chronicler_pool()

    since = _NOW
    result = await adapter.project(pool, chronicler_pool=cp, since=since)

    assert result.rows_projected == 0
    assert result.watermark == since
