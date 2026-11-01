"""Ingest writes the sender-authentication verdict on the event row (bu-s11n0s.20).

Drives the real ``ingest_v1`` against a recording fake pool (no database): the
``public.ingestion_events`` INSERT must carry the verdict as its last bind argument.
Real-Postgres coverage is in ``tests/integration/test_sender_authentication_roundtrip.py``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_INSERT = "INSERT INTO public.ingestion_events"


class _FakePool:
    def __init__(self) -> None:
        self.inserts: list[tuple] = []
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetchval = AsyncMock(return_value=None)

        async def execute(sql, *args):
            if _INSERT in sql:
                self.inserts.append((sql, args))

        conn.execute = execute

        @asynccontextmanager
        async def transaction():
            yield

        conn.transaction = transaction
        self._conn = conn
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


def _envelope(channel: str, sender: str, raw: dict | None) -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": channel,
            "provider": "gmail" if channel == "email" else "telegram",
            "endpoint_identity": "owner@example.org",
        },
        "event": {
            "external_event_id": f"event-{uuid.uuid4()}",
            "external_thread_id": "thread-1",
            "external_conversation_id": "telegram:12345",
            "reply_target_ref": "12345:1",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": sender},
        "payload": {"raw": raw, "normalized_text": "Subject: Invoice\n\nhello"},
        "control": {"idempotency_key": f"k-{uuid.uuid4()}"},
    }


def _headers(value: str) -> dict:
    return {"payload": {"headers": [{"name": "Authentication-Results", "value": value}]}}


async def _ingest(envelope: dict) -> tuple:
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    pool = _FakePool()
    with patch("butlers.fleet_events.publish_fleet_event", AsyncMock()):
        response = await ingest_v1(pool, envelope)
    assert response.status == "accepted"
    assert len(pool.inserts) == 1
    sql, args = pool.inserts[0]
    assert "sender_authentication" in sql
    return args


@pytest.mark.parametrize(
    ("auth_results", "expected"),
    [
        ("mx; dkim=pass header.d=vendor.example", "authenticated"),
        ("mx; dmarc=fail header.from=vendor.example", "failed"),
        ("mx; dkim=pass header.d=other.example", "unverified"),
    ],
)
async def test_an_email_ingest_stores_the_verdict(auth_results, expected):
    args = await _ingest(_envelope("email", "billing@vendor.example", _headers(auth_results)))

    assert args[-1] == expected


async def test_an_email_without_a_kept_header_stores_unverified():
    args = await _ingest(_envelope("email", "billing@vendor.example", {"text": "hi"}))

    assert args[-1] == "unverified"


async def test_a_telegram_ingest_stores_null():
    args = await _ingest(
        _envelope("telegram_bot", "billing@vendor.example", _headers("mx; dkim=pass header.d=a.b"))
    )

    assert args[-1] is None


async def test_a_metadata_tier_email_with_no_raw_payload_stores_unverified():
    envelope = _envelope("email", "billing@vendor.example", None)
    envelope["control"]["ingestion_tier"] = "metadata"
    envelope["control"]["policy_tier"] = "interactive"

    args = await _ingest(envelope)

    assert args[-1] == "unverified"


async def test_a_verifier_failure_stores_null_and_still_accepts(caplog):
    with (
        caplog.at_level("WARNING"),
        patch(
            "butlers.tools.switchboard.ingestion.ingest.sender_authentication_verdict",
            side_effect=RuntimeError("boom"),
        ),
    ):
        args = await _ingest(_envelope("email", "billing@vendor.example", {"text": "hi"}))

    assert args[-1] is None
    assert any(
        r.levelname == "WARNING" and "sender-authentication verdict failed" in r.getMessage()
        for r in caplog.records
    )
