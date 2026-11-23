"""The OwnTracks inbox-only recovery dry run's SQL against real PostgreSQL (bu-uhbhzq.4 S5).

A candidate is an OwnTracks location inbox row whose key is in neither ``owntracks_points`` nor
the retention tombstones. Rows on other channels are ignored, a resent key counts once, and the
read leaves every table as it was.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "owntracks_inbox_recovery.py"
_spec = importlib.util.spec_from_file_location("owntracks_inbox_recovery_it", _SCRIPT)
assert _spec is not None and _spec.loader is not None
recovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recovery)

_BASE = 1_750_000_000


def _key(n: int) -> str:
    return f"owntracks:owntracks:synthetic:{_BASE + n}:location"


@pytest.fixture
async def seeded(db):
    pool = await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition(now())")
    return pool


async def _inbox(pool, key: str, text: str, raw, *, channel: str = "owntracks") -> None:
    await pool.execute(
        """
        INSERT INTO switchboard.message_inbox (received_at, request_context, raw_payload,
            normalized_text)
        VALUES (now(), $1::jsonb, $2::jsonb, $3)
        """,
        {"source_channel": channel, "idempotency_key": key},
        {"payload": {"raw": raw, "normalized_text": text}},
        text,
    )


async def test_the_candidates_exclude_stored_forgotten_and_foreign_rows(seeded) -> None:
    full = {"_type": "location", "lat": 1.5, "lon": 103.5, "tst": _BASE + 1}
    await _inbox(seeded, _key(1), "ignored", full)  # full tier: recovered from raw
    await _inbox(seeded, _key(2), "Location update: 1.5N, 103.5E, acc 5m", None)  # from text
    await _inbox(seeded, _key(2), "Location update: 1.5N, 103.5E, acc 5m", None)  # resent: once
    await _inbox(seeded, _key(3), recovery.WITHHELD_TEXT, None)  # withheld: not recoverable
    await _inbox(seeded, _key(4), "Location update: 1.5N, 103.5E", None)  # already in points
    await _inbox(seeded, _key(5), "Location update: 1.5N, 103.5E", None)  # forgotten by retention
    await _inbox(seeded, _key(6), "Location update: 1.5N, 103.5E", None, channel="telegram")
    await seeded.execute(
        "INSERT INTO connectors.owntracks_points (idempotency_key, ts, lat, lon, "
        "endpoint_identity) VALUES ($1, now(), 1.5, 103.5, 'owntracks:synthetic')",
        _key(4),
    )
    await seeded.execute(
        "INSERT INTO switchboard.location_retention_forgotten_keys "
        "(logical_source_key, grant_id) VALUES ($1, gen_random_uuid())",
        _key(5),
    )
    before = await seeded.fetchval("SELECT count(*) FROM switchboard.message_inbox")

    async with seeded.acquire() as conn:
        report = await recovery.collect(conn)

    assert report["mode"] == "dry-run"
    assert report["candidates"] == 3
    assert report["recoverable"] == {"from_raw": 1, "from_text": 1}
    assert report["not_recoverable"] == {"unrecoverable_withheld": 1}
    assert await seeded.fetchval("SELECT count(*) FROM switchboard.message_inbox") == before
    assert await seeded.fetchval("SELECT count(*) FROM connectors.owntracks_points") == 1


async def test_a_write_inside_the_read_transaction_is_refused(seeded) -> None:
    import asyncpg

    async with seeded.acquire() as conn:
        async with conn.transaction(readonly=True):
            with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
                await conn.execute("DELETE FROM switchboard.message_inbox")
