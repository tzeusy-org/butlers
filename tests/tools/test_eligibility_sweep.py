"""Tests for the eligibility sweep (butler liveness TTL transitions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 2, 19, 12, 0, 0, tzinfo=UTC)


def _make_row(**kwargs) -> dict:
    return {
        "name": "test-butler",
        "eligibility_state": "active",
        "liveness_ttl_seconds": 300,
        "last_seen_at": None,
        "quarantined_at": None,
        "quarantine_reason": None,
        **kwargs,
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
    sweep = next((s for s in schedules if s["name"] == "eligibility_sweep"), None)
    assert sweep is not None
    assert sweep["cron"] == "*/5 * * * *"


def test_butler_toml_has_decision_review_schedules():
    """bu-ckkpz.4: weekly decision-review digest + 6h P1/deploy escalation check."""
    import tomllib
    from pathlib import Path

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])

    digest = next((s for s in schedules if s["name"] == "decision-review-digest"), None)
    assert digest is not None
    assert digest["cron"] == "0 9 * * 1"
    assert digest["dispatch_mode"] == "job"
    assert digest["job_name"] == "decision_review_digest"

    escalation = next((s for s in schedules if s["name"] == "decision-escalation-check"), None)
    assert escalation is not None
    assert escalation["cron"] == "0 */6 * * *"
    assert escalation["dispatch_mode"] == "job"
    assert escalation["job_name"] == "decision_escalation_check"


async def test_eligibility_sweep_state_transitions():
    """Butler transitions to stale/quarantined when TTL thresholds are exceeded."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    cases = [
        ("active", 400, "stale"),
        ("stale", 700, "quarantined"),
        ("active", 700, "quarantined"),
    ]
    for state, elapsed_secs, expected_new_state in cases:
        row = _make_row(
            eligibility_state=state,
            liveness_ttl_seconds=300,
            last_seen_at=_NOW - timedelta(seconds=elapsed_secs),
        )
        pool = _make_pool([row])
        with patch(
            "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
            new_callable=AsyncMock,
        ):
            result = await run_eligibility_sweep(pool, now=_NOW)
        assert result["transitioned"] == 1
        assert result["transitions"][0]["new_state"] == expected_new_state, (
            f"Failed for {state}/{elapsed_secs}"
        )


async def test_eligibility_sweep_no_transition_and_skip():
    """Butler within TTL is not transitioned; NULL last_seen_at is skipped."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    # Within TTL
    row = _make_row(
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=_NOW - timedelta(seconds=100),
    )
    pool = _make_pool([row])
    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)
    assert result["transitioned"] == 0

    # NULL last_seen_at
    pool2 = _make_pool([_make_row(last_seen_at=None)])
    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result2 = await run_eligibility_sweep(pool2, now=_NOW)
    assert result2["skipped"] == 1
    assert result2["transitioned"] == 0


class TestDeriveEligibilityStateClockSkew:
    """_derive_eligibility_state bounds the TTL window against a future last_seen_at.

    Without the 5-minute future-skew guard, a future-dated last_seen_at (clock
    skew between a butler host and the DB, or a bad writer) satisfies
    ``last_seen_at + ttl >= now`` forever and keeps the butler eligible
    indefinitely.
    """

    def test_fresh_last_seen_is_active(self):
        from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

        row = _make_row(last_seen_at=_NOW - timedelta(seconds=100), liveness_ttl_seconds=300)
        assert _derive_eligibility_state(row, now=_NOW) == "active"

    def test_past_ttl_is_stale(self):
        from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

        row = _make_row(last_seen_at=_NOW - timedelta(seconds=400), liveness_ttl_seconds=300)
        assert _derive_eligibility_state(row, now=_NOW) == "stale"

    def test_future_within_tolerance_is_active(self):
        from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

        # 1 min into the future — within the 5-min skew tolerance, still trusted.
        row = _make_row(last_seen_at=_NOW + timedelta(minutes=1), liveness_ttl_seconds=300)
        assert _derive_eligibility_state(row, now=_NOW) == "active"

    def test_future_beyond_tolerance_is_stale(self):
        from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

        # 10 min into the future — beyond the 5-min tolerance. Without the guard
        # the unbounded TTL window would report "active"; it must be "stale".
        row = _make_row(last_seen_at=_NOW + timedelta(minutes=10), liveness_ttl_seconds=300)
        assert _derive_eligibility_state(row, now=_NOW) == "stale"
