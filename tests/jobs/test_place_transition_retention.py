"""prune_place_transitions: dark behind the flag, never touches the DB unless fully enabled."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs.retention import prune_place_transitions
from butlers.perception import flags

pytestmark = pytest.mark.unit

COUNTS = {"dry_run": False, "events": 3, "retractions": 1, "receipts": 4}


def _conn(raw=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=json.dumps(raw or COUNTS))
    return conn


async def test_the_module_default_is_dark():
    assert flags.PLACE_RETENTION_LIVE is False


@pytest.mark.parametrize("enabled", [True, False])
async def test_with_the_flag_off_nothing_touches_the_database(enabled):
    conn = _conn()
    result = await prune_place_transitions(conn, enabled=enabled, dry_run=False)
    assert result["enabled"] is False and result["events"] == 0
    conn.fetchval.assert_not_awaited()


async def test_a_disabled_job_does_nothing_even_with_the_flag_on(monkeypatch):
    monkeypatch.setattr(flags, "PLACE_RETENTION_LIVE", True)
    conn = _conn()
    assert (await prune_place_transitions(conn))["enabled"] is False
    conn.fetchval.assert_not_awaited()


async def test_enabled_and_live_calls_the_one_function_with_the_bound(monkeypatch):
    monkeypatch.setattr(flags, "PLACE_RETENTION_LIVE", True)
    conn = _conn()
    result = await prune_place_transitions(conn, enabled=True, dry_run=False, batch_limit=25)
    conn.fetchval.assert_awaited_once_with(
        "SELECT public.perception_purge_place_history($1, $2)", 25, False
    )
    assert result == {
        "enabled": True,
        "dry_run": False,
        "events": 3,
        "retractions": 1,
        "receipts": 4,
    }


async def test_dry_run_is_the_default_and_asks_for_a_count_only(monkeypatch):
    monkeypatch.setattr(flags, "PLACE_RETENTION_LIVE", True)
    conn = _conn({**COUNTS, "dry_run": True})
    result = await prune_place_transitions(conn, enabled=True)
    assert conn.fetchval.await_args.args[2] is True and result["dry_run"] is True


async def test_a_non_integer_batch_is_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(flags, "PLACE_RETENTION_LIVE", True)
    conn = _conn()
    with pytest.raises(ValueError):
        await prune_place_transitions(conn, enabled=True, batch_limit="5")  # type: ignore[arg-type]
    conn.fetchval.assert_not_awaited()
