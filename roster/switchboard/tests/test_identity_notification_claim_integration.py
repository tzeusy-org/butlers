"""Database proof for the unknown-sender owner-notification claim."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from butlers.tools.switchboard.identity.inject import _claim_unknown_sender_notification

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_unknown_sender_notification_claim_is_durable_and_single_winner(db) -> None:
    """Competing pool connections can persist only one notification permission."""
    pool = await db(
        "core",
        "switchboard",
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        schema="switchboard",
    )
    first, second = await asyncio.gather(
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
    )

    assert sorted((first, second)) == [False, True]
    row = await pool.fetchrow(
        "SELECT value, version FROM state WHERE key = $1",
        "identity:unknown_notified:telegram:12345",
    )
    assert row is not None
    assert row["value"] == {"unknown_sender_notification_attempted": True}
    assert row["version"] == 1
