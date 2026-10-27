"""The in-lock dedupe re-check honours the withheld-bearer skip/omit rule.

Drives ingest_v1 into the advisory-lock block with a fake pool and records the locks taken, the
in-lock lookups, and the request_context stored by the message_inbox INSERT. Synthetic data only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.testing.fake_pool import AsyncCtx
from butlers.tools.switchboard.ingestion import ingest as ingest_module
from butlers.tools.switchboard.ingestion.ingest import ingest_v1

pytestmark = pytest.mark.unit

PLAIN = "Lunch on Thursday works for me."
CODE = "Your code is 482913. It expires in 10 minutes."
PRE = "Your code is [auth-code withheld: example.com]. It expires in 10 minutes."


class _Stop(Exception):
    pass


def _envelope(text: str, *, bearer_scrubbed: bool = False) -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": "owner@example.org",
        },
        "event": {
            "external_event_id": "evt-1",
            "observed_at": datetime(2026, 10, 18, 9, 30, tzinfo=UTC).isoformat(),
        },
        "sender": {"identity": "no-reply@example.com"},
        "payload": {"raw": {}, "normalized_text": text},
        "control": {"bearer_scrubbed": bearer_scrubbed},
    }


async def _drive(text: str, *, bearer_scrubbed: bool = False) -> dict:
    seen: dict = {"locks": [], "lookups": [], "stored": None}
    conn = MagicMock()

    async def execute(sql, *args):
        if "pg_advisory_xact_lock" in sql:
            seen["locks"].append(args[0])
            return None
        if "INSERT INTO switchboard.message_inbox" in sql:
            seen["stored"] = args[2]
            raise _Stop
        return None

    async def fetchrow(sql, *args):
        seen["lookups"].append("content_hash_key" if "content_hash_key" in sql else "dedupe_key")
        return None

    conn.execute = execute
    conn.fetchrow = fetchrow

    conn.transaction = lambda: AsyncCtx(None)

    pool = MagicMock()
    pool.acquire = lambda: AsyncCtx(conn)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    with (
        patch.object(ingest_module, "_find_request_by_dedupe_key", AsyncMock(return_value=None)),
        patch.object(ingest_module, "_find_request_by_content_hash", AsyncMock(return_value=None)),
    ):
        # ingest wraps persistence failures; the sentinel is the cause.
        with pytest.raises(RuntimeError) as raised:
            await ingest_v1(pool, _envelope(text, bearer_scrubbed=bearer_scrubbed))
        assert isinstance(raised.value.__cause__, _Stop)
    return seen


async def test_plain_text_takes_both_locks_and_stores_the_content_hash_key() -> None:
    seen = await _drive(PLAIN)

    assert len(seen["locks"]) == 2
    assert seen["lookups"] == ["dedupe_key", "content_hash_key"]
    assert "content_hash_key" in seen["stored"]


@pytest.mark.parametrize(("text", "flag"), [(CODE, False), (PRE, True), (PRE, False)])
async def test_withheld_text_skips_the_in_lock_content_hash_path(text: str, flag: bool) -> None:
    seen = await _drive(text, bearer_scrubbed=flag)

    assert len(seen["locks"]) == 1
    assert seen["lookups"] == ["dedupe_key"]
    assert "content_hash_key" not in seen["stored"]
    assert "482913" not in str(seen["stored"])
