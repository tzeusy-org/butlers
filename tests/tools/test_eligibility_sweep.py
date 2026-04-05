"""Tests for the eligibility sweep (butler liveness TTL transitions).

Key contracts:
1. Active butler exceeding TTL → stale
2. Stale butler exceeding 2x TTL → quarantined
3. Active butler within TTL → unchanged
4. Butler with NULL last_seen_at → skipped
5. Transitions logged to eligibility log
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 2, 19, 12, 0, 0, tzinfo=UTC)


def _make_row(**kwargs) -> dict:
    return {
        "name": "test-butler", "eligibility_state": "active",
        "liveness_ttl_seconds": 300, "last_seen_at": None,
        "quarantined_at": None, "quarantine_reason": None, **kwargs,
    }


def _make_pool(rows: list, *, execute_return: str = "UPDATE 1") -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


def test_butler_toml_has_eligibility_sweep_schedule():
    """eligibility_sweep task is defined in butler.toml with 5-minute cron."""
    import tomllib
    from pathlib import Path

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    names = [s["name"] for s in schedules]
    assert "eligibility_sweep" in names

    sweep = next(s for s in schedules if s["name"] == "eligibility_sweep")
    assert sweep["cron"] == "*/5 * * * *"


@pytest.mark.parametrize(
    "state, elapsed_secs, expected_new_state",
    [
        ("active", 400, "stale"),       # past 1x TTL
        ("stale", 700, "quarantined"),  # past 2x TTL
        ("active", 700, "quarantined"), # past 2x TTL directly
    ],
)
async def test_eligibility_sweep_state_transition(state, elapsed_secs, expected_new_state):
    """Butler transitions to expected state when TTL thresholds are exceeded."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    row = _make_row(
        eligibility_state=state,
        liveness_ttl_seconds=300,
        last_seen_at=_NOW - timedelta(seconds=elapsed_secs),
    )
    pool = _make_pool([row])

    with patch("butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
               new_callable=AsyncMock):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 1
    t = result["transitions"][0]
    assert t["previous_state"] == state
    assert t["new_state"] == expected_new_state


async def test_eligibility_sweep_within_ttl_unchanged():
    """Active butler within TTL is not transitioned."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    row = _make_row(
        eligibility_state="active", liveness_ttl_seconds=300,
        last_seen_at=_NOW - timedelta(seconds=100),  # within 300s TTL
    )
    pool = _make_pool([row])

    with patch("butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
               new_callable=AsyncMock):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 0
    pool.execute.assert_not_called()


async def test_eligibility_sweep_skips_null_last_seen_at():
    """Butler with NULL last_seen_at is skipped (not transitioned)."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    row = _make_row(last_seen_at=None)
    pool = _make_pool([row])

    with patch("butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
               new_callable=AsyncMock):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["skipped"] == 1
    assert result["transitioned"] == 0
    pool.execute.assert_not_called()


async def test_eligibility_sweep_logs_transitions():
    """Transitions are logged to eligibility log via audit function."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    row = _make_row(eligibility_state="active", liveness_ttl_seconds=300,
                    last_seen_at=_NOW - timedelta(seconds=400))
    pool = _make_pool([row])

    with patch("butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
               new_callable=AsyncMock) as mock_audit:
        await run_eligibility_sweep(pool, now=_NOW)

    mock_audit.assert_called_once()
    kwargs = mock_audit.call_args.kwargs
    assert kwargs["new_state"] == "stale"
    assert kwargs["reason"] == "liveness_ttl_expired"
