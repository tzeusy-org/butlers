"""The evidence journal is committed before a connector submits (bu-uhbhzq.4 S3).

Parametrized over every adopting connector's submit path: a journal failure submits nothing; a
submit failure keeps the row; a duplicate delivery leaves one row and a new provider revision
adds a second; the stored raw is the envelope's inbox-equivalent at its tier.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors import evidence_journal as journal
from butlers.connectors.evidence_journal import EvidenceNotCommitted
from butlers.connectors.gmail import GmailConnectorRuntime
from butlers.connectors.whatsapp_user_client import WhatsAppUserClientConnector

pytestmark = pytest.mark.unit


def _envelope(channel: str, tier: str = "full", event_id: str = "m-1") -> dict[str, Any]:
    return {
        "schema_version": "ingest.v1",
        "source": {"channel": channel, "provider": channel, "endpoint_identity": "acct:synthetic"},
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "t-1",
            "observed_at": "2026-11-20T10:00:00+00:00",
        },
        "sender": {"identity": "alice@example.test"},
        "payload": {
            "raw": {"subject": "hello"} if tier == "full" else None,
            "normalized_text": "hello there",
        },
        "control": {
            "idempotency_key": f"{channel}:acct:synthetic:{event_id}",
            "ingestion_tier": tier,
            "policy_tier": "default",
        },
    }


class _Journal:
    """Stands in for the journal table: unique on the identity key; can be made to fail."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], tuple[Any, ...]] = {}
        self.fail: Exception | None = None
        self.order: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.order.append("journal")
        if self.fail is not None:
            raise self.fail
        key = (args[0], args[1], args[2], args[3])
        if key in self.rows:
            return None
        self.rows[key] = args
        return len(self.rows)


def _gmail(pool: Any, call_tool: AsyncMock) -> GmailConnectorRuntime:
    connector = object.__new__(GmailConnectorRuntime)
    connector._db_pool = pool
    connector._mcp_client = MagicMock(call_tool=call_tool)
    connector._metrics = MagicMock()
    connector._last_ingest_submit = None
    return connector


def _whatsapp(pool: Any, call_tool: AsyncMock) -> WhatsAppUserClientConnector:
    connector = object.__new__(WhatsAppUserClientConnector)
    connector._db_pool = pool
    connector._mcp_client = MagicMock(call_tool=call_tool)
    return connector


_ADOPTERS = [
    pytest.param(_gmail, "gmail", "_submit_to_ingest_api", id="gmail"),
    pytest.param(_whatsapp, "whatsapp", "_submit_to_ingest", id="whatsapp-user"),
]


def _ordered_call_tool(pool: _Journal, *, fail: bool = False) -> AsyncMock:
    async def call_tool(tool: str, payload: dict | None = None) -> dict:
        pool.order.append("submit")
        if fail:
            raise ConnectionError("switchboard unreachable")
        return {"status": "accepted"}

    return AsyncMock(side_effect=call_tool)


@pytest.mark.parametrize(("build", "channel", "method"), _ADOPTERS)
async def test_the_row_is_committed_before_the_submit(build, channel, method) -> None:
    pool = _Journal()
    connector = build(pool, _ordered_call_tool(pool))
    await getattr(connector, method)(_envelope(channel))
    assert pool.order == ["journal", "submit"]
    ((key, _args),) = [(k, v) for k, v in pool.rows.items()]
    assert key[0] == channel and key[2] == "m-1"


@pytest.mark.parametrize(("build", "channel", "method"), _ADOPTERS)
async def test_a_failed_journal_write_submits_nothing(build, channel, method) -> None:
    pool = _Journal()
    pool.fail = OSError("connectors database unreachable")
    call_tool = _ordered_call_tool(pool)
    connector = build(pool, call_tool)
    with pytest.raises(EvidenceNotCommitted):
        await getattr(connector, method)(_envelope(channel))
    call_tool.assert_not_awaited()


@pytest.mark.parametrize(("build", "channel", "method"), _ADOPTERS)
async def test_a_failed_submit_keeps_the_row(build, channel, method) -> None:
    pool = _Journal()
    connector = build(pool, _ordered_call_tool(pool, fail=True))
    with pytest.raises((ConnectionError, RuntimeError)):
        await getattr(connector, method)(_envelope(channel))
    assert len(pool.rows) == 1


@pytest.mark.parametrize(("build", "channel", "method"), _ADOPTERS)
async def test_a_duplicate_delivery_is_one_row(build, channel, method) -> None:
    pool = _Journal()
    connector = build(pool, _ordered_call_tool(pool))
    for _ in range(3):
        await getattr(connector, method)(_envelope(channel))
    assert len(pool.rows) == 1


async def test_a_new_provider_revision_appends_a_second_row() -> None:
    pool = _Journal()
    envelope = _envelope("gmail")
    assert await journal.commit_envelope(pool, envelope) is True
    assert await journal.commit_envelope(pool, envelope) is False
    assert await journal.commit_envelope(pool, envelope, provider_revision="r2") is True
    assert len(pool.rows) == 2


@pytest.mark.parametrize(("build", "channel", "method"), _ADOPTERS)
async def test_with_no_database_the_submit_still_runs(build, channel, method) -> None:
    call_tool = AsyncMock(return_value={"status": "accepted"})
    await getattr(build(None, call_tool), method)(_envelope(channel))
    call_tool.assert_awaited_once()


async def test_the_stored_raw_is_the_inbox_equivalent_at_each_tier() -> None:
    for tier in ("full", "metadata"):
        pool = _Journal()
        envelope = _envelope("gmail", tier=tier)
        before = copy.deepcopy(envelope)
        await journal.commit_envelope(pool, envelope)
        (row,) = pool.rows.values()
        raw = _decode(row[9])
        assert raw["payload"]["raw"] == envelope["payload"]["raw"]  # null at the metadata tier
        assert raw["control"]["ingestion_tier"] == tier
        assert row[7] == tier
        assert set(raw) == {"source", "event", "sender", "payload", "control"}  # nothing extra
        assert envelope == before  # the envelope is not mutated


async def test_bearer_material_is_scrubbed_and_nul_is_stripped() -> None:
    pool = _Journal()
    envelope = _envelope("gmail")
    envelope["payload"]["normalized_text"] = "Your verification code is 482913\x00"
    envelope["payload"]["raw"] = {"snippet": "Your verification code is 482913"}
    await journal.commit_envelope(pool, envelope)
    (row,) = pool.rows.values()
    assert "482913" not in row[9]
    assert "\\u0000" not in row[9]


async def test_an_unsupported_tier_is_refused_and_writes_nothing() -> None:
    pool = _Journal()
    envelope = _envelope("gmail")
    envelope["control"]["ingestion_tier"] = "skip"
    with pytest.raises(ValueError):
        await journal.commit_envelope(pool, envelope)
    assert pool.rows == {} and pool.order == []


def _decode(text: str) -> dict[str, Any]:
    import json

    return json.loads(text)
