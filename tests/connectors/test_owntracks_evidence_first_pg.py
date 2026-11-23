"""OwnTracks commits its location row before it submits, on real PostgreSQL (bu-uhbhzq.4).

Acceptance 1: when the ``owntracks_points`` insert fails, the webhook answers non-2xx, ingest is
never called and the checkpoint is unchanged; on the happy path the row exists before the
submit, asserted through call order against the real table.
"""

from __future__ import annotations

import shutil
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.connectors.test_owntracks_ingest_failure import (
    _POINT,
    _connector,
    _ingests,
    _post,
    _stop,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_COUNT = "SELECT count(*) FROM connectors.owntracks_points"


async def test_the_row_exists_before_the_submit_is_called(db_pool: Any) -> None:
    seen_at_submit: list[int] = []

    async def call_tool(tool: str, payload: dict | None = None) -> dict:
        if tool == "ingest":
            seen_at_submit.append(await db_pool.fetchval(_COUNT))
        return {"status": "accepted"}

    connector = _connector(AsyncMock(side_effect=call_tool), db_pool)
    try:
        assert (await _post(connector, _POINT)).status_code == 200
        assert seen_at_submit == [1]  # the row was already committed when ingest ran
    finally:
        await _stop(connector)


async def test_a_failed_insert_submits_nothing_and_keeps_the_checkpoint(db_pool: Any) -> None:
    await db_pool.execute("DROP TABLE connectors.owntracks_points CASCADE")  # injected DB error
    call_tool = AsyncMock(return_value={"status": "accepted"})
    connector = _connector(call_tool, db_pool)
    saved = AsyncMock()
    connector._save_checkpoint = saved  # type: ignore[method-assign]
    try:
        response = await _post(connector, _POINT)
        assert response.status_code == 503
        assert _ingests(call_tool) == []
        saved.assert_not_awaited()
    finally:
        await _stop(connector)


async def test_a_nul_in_the_ssid_is_stored_without_it(db_pool: Any) -> None:
    connector = _connector(AsyncMock(return_value={"status": "accepted"}), db_pool)
    try:
        body = {**_POINT, "SSID": "Cafe\x00Wifi"}
        assert (await _post(connector, body)).status_code == 200
        stored = await db_pool.fetchval(
            "SELECT raw_payload ->> 'SSID' FROM connectors.owntracks_points"
        )
        assert stored == "CafeWifi"
    finally:
        await _stop(connector)
