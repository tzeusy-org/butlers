"""Tests for the eligibility sweep — butlers-976.4.

Covers all acceptance criteria:
1. eligibility-sweep task synced to scheduled_tasks on startup
2. Active butler exceeding TTL transitions to stale
3. Stale butler exceeding 2x TTL transitions to quarantined
4. Active butler within TTL is unchanged
5. Butler with NULL last_seen_at is skipped
6. All transitions logged to butler_registry_eligibility_log
7. Tests cover all transition scenarios

Uses mocked asyncpg.Pool so tests run without Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs) -> dict:
    """Build a minimal butler_registry-like row dict."""
    defaults = {
        "name": "test-butler",
        "eligibility_state": "active",
        "liveness_ttl_seconds": 300,
        "last_seen_at": None,
        "quarantined_at": None,
        "quarantine_reason": None,
    }
    defaults.update(kwargs)
    return defaults


def _make_pool(rows: list[dict], *, execute_return: str = "UPDATE 1") -> AsyncMock:
    """Return a mocked asyncpg.Pool that yields *rows* from pool.fetch()."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


_NOW = datetime(2026, 2, 19, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. butler.toml — eligibility-sweep schedule entry
# ---------------------------------------------------------------------------


def test_butler_toml_has_eligibility_sweep_schedule():
    """Acceptance criterion 1: eligibility-sweep task defined in butler.toml."""
    import tomllib
    from pathlib import Path

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    names = [s["name"] for s in schedules]
    assert "eligibility-sweep" in names, f"eligibility-sweep not in schedules: {names}"

    sweep = next(s for s in schedules if s["name"] == "eligibility-sweep")
    assert sweep["cron"] == "*/5 * * * *", f"Unexpected cron: {sweep['cron']}"
    if "prompt" in sweep:
        assert "run_eligibility_sweep" in sweep["prompt"], (
            f"Prompt does not mention run_eligibility_sweep: {sweep['prompt']}"
        )
    else:
        assert sweep.get("dispatch_mode") == "job", (
            f"Eligibility sweep should use job dispatch mode: {sweep}"
        )
        assert sweep.get("job_name") == "eligibility_sweep", (
            f"Eligibility sweep should target eligibility_sweep job: {sweep}"
        )


# ---------------------------------------------------------------------------
# 2. Active butler exceeding TTL → stale
# ---------------------------------------------------------------------------


async def test_active_butler_exceeding_ttl_transitions_to_stale():
    """Acceptance criterion 2: active butler past TTL transitions to stale."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=400)  # past 300s TTL
    row = _make_row(
        name="general",
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["evaluated"] == 1
    assert result["transitioned"] == 1
    assert result["skipped"] == 0

    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["butler"] == "general"
    assert t["previous_state"] == "active"
    assert t["new_state"] == "stale"

    # DB update called with stale state
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    assert "eligibility_state" in call_args.args[0]
    assert call_args.args[1] == "stale"

    # Audit log written
    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["previous_state"] == "active"
    assert audit_kwargs["new_state"] == "stale"
    assert audit_kwargs["reason"] == "liveness_ttl_expired"


# ---------------------------------------------------------------------------
# 3. Stale butler exceeding 2x TTL → quarantined
# ---------------------------------------------------------------------------


async def test_stale_butler_exceeding_2x_ttl_transitions_to_quarantined():
    """Acceptance criterion 3: stale butler past 2x TTL transitions to quarantined."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=700)  # past 2 * 300s = 600s
    row = _make_row(
        name="health",
        eligibility_state="stale",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 1
    t = result["transitions"][0]
    assert t["previous_state"] == "stale"
    assert t["new_state"] == "quarantined"
    assert "quarantine_reason" in t

    # DB update includes quarantined_at and quarantine_reason
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    sql = call_args.args[0]
    assert "quarantined_at" in sql
    assert "quarantine_reason" in sql
    # new_state param
    assert call_args.args[1] == "quarantined"

    # Audit log
    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["previous_state"] == "stale"
    assert audit_kwargs["new_state"] == "quarantined"
    assert audit_kwargs["reason"] == "liveness_ttl_2x_expired"


# ---------------------------------------------------------------------------
# 3b. Active butler exceeding 2x TTL (no stale intermediate) → quarantined
# ---------------------------------------------------------------------------


async def test_active_butler_exceeding_2x_ttl_transitions_directly_to_quarantined():
    """Active butler past 2x TTL transitions directly to quarantined."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=700)
    row = _make_row(
        name="general",
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 1
    t = result["transitions"][0]
    assert t["previous_state"] == "active"
    assert t["new_state"] == "quarantined"


# ---------------------------------------------------------------------------
# 4. Active butler within TTL — unchanged
# ---------------------------------------------------------------------------


async def test_active_butler_within_ttl_is_unchanged():
    """Acceptance criterion 4: active butler within TTL is not modified."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=100)  # well within 300s TTL
    row = _make_row(
        name="general",
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["evaluated"] == 1
    assert result["transitioned"] == 0
    assert result["transitions"] == []

    pool.execute.assert_not_called()
    mock_audit.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Butler with NULL last_seen_at is skipped
# ---------------------------------------------------------------------------


async def test_butler_with_null_last_seen_at_is_skipped():
    """Acceptance criterion 5: butler with NULL last_seen_at is skipped entirely."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    row = _make_row(name="new-butler", last_seen_at=None, eligibility_state="active")
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["evaluated"] == 0
    assert result["skipped"] == 1
    assert result["transitioned"] == 0

    pool.execute.assert_not_called()
    mock_audit.assert_not_called()


# ---------------------------------------------------------------------------
# 6. All transitions logged to butler_registry_eligibility_log
# ---------------------------------------------------------------------------


async def test_transitions_logged_to_eligibility_log():
    """Acceptance criterion 6: every transition writes to butler_registry_eligibility_log."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    rows = [
        _make_row(
            name="alpha",
            eligibility_state="active",
            liveness_ttl_seconds=60,
            last_seen_at=_NOW - timedelta(seconds=80),
        ),
        _make_row(
            name="beta",
            eligibility_state="stale",
            liveness_ttl_seconds=60,
            last_seen_at=_NOW - timedelta(seconds=140),
        ),
    ]
    pool = _make_pool(rows)

    audit_calls: list = []

    async def _capture_audit(_pool, **kwargs):
        audit_calls.append(kwargs)

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        side_effect=_capture_audit,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 2

    # One audit call per transition
    assert len(audit_calls) == 2

    alpha_audit = next(c for c in audit_calls if c["name"] == "alpha")
    assert alpha_audit["previous_state"] == "active"
    assert alpha_audit["new_state"] == "stale"
    assert alpha_audit["observed_at"] == _NOW

    beta_audit = next(c for c in audit_calls if c["name"] == "beta")
    assert beta_audit["previous_state"] == "stale"
    assert beta_audit["new_state"] == "quarantined"
    assert beta_audit["observed_at"] == _NOW


# ---------------------------------------------------------------------------
# 7. Additional scenario coverage
# ---------------------------------------------------------------------------


async def test_quarantined_butler_is_not_modified():
    """Quarantined butlers are left untouched by the sweep."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=9999)
    row = _make_row(
        name="quarantined-butler",
        eligibility_state="quarantined",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
        quarantined_at=_NOW - timedelta(hours=1),
        quarantine_reason="manual quarantine",
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ) as mock_audit:
        result = await run_eligibility_sweep(pool, now=_NOW)

    # Quarantined butler is counted as evaluated but no transition occurs.
    assert result["evaluated"] == 1
    assert result["transitioned"] == 0
    pool.execute.assert_not_called()
    mock_audit.assert_not_called()


async def test_mixed_butler_states_correct_transitions():
    """Multiple butlers with different states are each handled correctly."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    rows = [
        # Within TTL — unchanged
        _make_row(
            name="alpha",
            eligibility_state="active",
            liveness_ttl_seconds=300,
            last_seen_at=_NOW - timedelta(seconds=50),
        ),
        # Past TTL but < 2x — active→stale
        _make_row(
            name="beta",
            eligibility_state="active",
            liveness_ttl_seconds=300,
            last_seen_at=_NOW - timedelta(seconds=400),
        ),
        # Past 2x TTL — stale→quarantined
        _make_row(
            name="gamma",
            eligibility_state="stale",
            liveness_ttl_seconds=300,
            last_seen_at=_NOW - timedelta(seconds=700),
        ),
        # NULL last_seen_at — skipped
        _make_row(
            name="delta",
            eligibility_state="active",
            last_seen_at=None,
        ),
        # Quarantined — untouched
        _make_row(
            name="epsilon",
            eligibility_state="quarantined",
            liveness_ttl_seconds=300,
            last_seen_at=_NOW - timedelta(seconds=9999),
        ),
    ]
    pool = _make_pool(rows)

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    # alpha: within TTL — evaluated but no transition
    # beta: active→stale — transition
    # gamma: stale→quarantined — transition
    # delta: NULL last_seen_at — skipped
    # epsilon: quarantined — evaluated but no transition (quarantine guard)
    assert result["evaluated"] == 4  # alpha, beta, gamma, epsilon
    assert result["skipped"] == 1  # delta
    assert result["transitioned"] == 2  # beta, gamma

    names = {t["butler"] for t in result["transitions"]}
    assert names == {"beta", "gamma"}


async def test_empty_registry_returns_zero_counts():
    """Empty registry produces zeroed result without errors."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    pool = _make_pool([])

    result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["evaluated"] == 0
    assert result["skipped"] == 0
    assert result["transitioned"] == 0
    assert result["transitions"] == []
    pool.execute.assert_not_called()


async def test_custom_liveness_ttl_respected():
    """Non-default liveness_ttl_seconds values are respected."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    # 60s TTL: butler seen 80s ago → stale (not yet 2x=120s → quarantined)
    row = _make_row(
        name="fast-ttl-butler",
        eligibility_state="active",
        liveness_ttl_seconds=60,
        last_seen_at=_NOW - timedelta(seconds=80),
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    assert result["transitioned"] == 1
    t = result["transitions"][0]
    assert t["new_state"] == "stale"
    assert t["liveness_ttl_seconds"] == 60


async def test_transition_detail_includes_elapsed_seconds():
    """Transition detail includes elapsed_seconds for observability."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    elapsed = 450
    row = _make_row(
        name="general",
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=_NOW - timedelta(seconds=elapsed),
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    t = result["transitions"][0]
    assert t["elapsed_seconds"] == elapsed
    assert t["last_seen_at"] == (_NOW - timedelta(seconds=elapsed)).isoformat()


async def test_quarantine_transition_sets_quarantine_reason():
    """When transitioning to quarantined, quarantine_reason is populated."""
    from butlers.tools.switchboard.registry.sweep import run_eligibility_sweep

    last_seen_at = _NOW - timedelta(seconds=700)
    row = _make_row(
        name="health",
        eligibility_state="active",
        liveness_ttl_seconds=300,
        last_seen_at=last_seen_at,
    )
    pool = _make_pool([row])

    with patch(
        "butlers.tools.switchboard.registry.sweep._audit_eligibility_transition",
        new_callable=AsyncMock,
    ):
        result = await run_eligibility_sweep(pool, now=_NOW)

    t = result["transitions"][0]
    assert t["new_state"] == "quarantined"
    assert "quarantine_reason" in t
    assert "liveness_ttl_seconds=300" in t["quarantine_reason"]
    assert last_seen_at.isoformat() in t["quarantine_reason"]


# ---------------------------------------------------------------------------
# Sweep helper unit tests
# ---------------------------------------------------------------------------


def test_sweep_transition_reason_stale():
    from butlers.tools.switchboard.registry.sweep import _sweep_transition_reason

    assert _sweep_transition_reason("active", "stale") == "liveness_ttl_expired"


def test_sweep_transition_reason_quarantined():
    from butlers.tools.switchboard.registry.sweep import _sweep_transition_reason

    assert _sweep_transition_reason("stale", "quarantined") == "liveness_ttl_2x_expired"
    assert _sweep_transition_reason("active", "quarantined") == "liveness_ttl_2x_expired"


def test_sweep_transition_reason_fallback():
    from butlers.tools.switchboard.registry.sweep import _sweep_transition_reason

    assert _sweep_transition_reason("active", "active") == "sweep_transition"
