"""Tests for GET /api/butlers/board (bu-86c4c.17).

Covers the canonical liveness derivation, the cron-expectation join
("overdue" vs "on_schedule"), stable roster ordering, and the degraded-mode
aggregate contract (partial cost sum + explicit source-error flags rather
than a confident zero).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from butlers.api.deps import ButlerConnectionInfo, get_pricing
from butlers.api.routers.butlers import _cadence_label, _get_db_manager
from butlers.core.pricing import ModelPricing, PricingConfig

from .conftest import make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


def _now() -> datetime:
    """Wall-clock 'now', matching the endpoint's own uninjectable clock.

    The board endpoint calls ``datetime.now(UTC)`` internally with no way to
    inject a fixed clock, so tests anchor fixture timestamps to real "now"
    (captured once per test, a few milliseconds before the endpoint's own
    call) rather than a hardcoded date -- deltas of minutes/hours/days still
    hold correctly against that negligible skew.
    """
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeButlerPool:
    """Minimal stand-in for a butler-scoped asyncpg pool.

    Dispatches on distinguishing substrings of the SQL text -- mirrors the
    handful of queries the board endpoint issues per butler.
    """

    def __init__(
        self,
        *,
        crons: list[str] | None = None,
        active_count: int = 0,
        last_completed_at: datetime | None = None,
        max_concurrent: int | None = None,
        hourly_counts: list[int] | None = None,
        cost_model: str = "claude-x",
        cost_input_tokens: int = 0,
        cost_output_tokens: int = 0,
        session_query_fails: bool = False,
        cost_query_fails: bool = False,
        hourly_query_fails: bool = False,
    ) -> None:
        self.crons = crons or []
        self.active_count = active_count
        self.last_completed_at = last_completed_at
        self.max_concurrent = max_concurrent
        self.hourly_counts = hourly_counts or [0] * 24
        self.cost_model = cost_model
        self.cost_input_tokens = cost_input_tokens
        self.cost_output_tokens = cost_output_tokens
        self.session_query_fails = session_query_fails
        self.cost_query_fails = cost_query_fails
        self.hourly_query_fails = hourly_query_fails

    async def fetchrow(self, sql, *args):
        if "completed_at FROM sessions" in sql:
            if self.session_query_fails:
                raise RuntimeError("schema unreachable")
            if self.last_completed_at is None:
                return None
            return {"completed_at": self.last_completed_at}
        if "max_concurrent FROM runtime_config" in sql:
            if self.max_concurrent is None:
                return None
            return {"max_concurrent": self.max_concurrent}
        if "total_sessions" in sql:
            if self.cost_query_fails:
                raise RuntimeError("cost query failed")
            return {
                "total_sessions": 1,
                "total_input_tokens": self.cost_input_tokens,
                "total_output_tokens": self.cost_output_tokens,
                "total_cached_input_tokens": 0,
                "total_cache_creation_tokens": 0,
            }
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "count(*) FROM sessions WHERE completed_at IS NULL" in sql:
            if self.session_query_fails:
                raise RuntimeError("schema unreachable")
            return self.active_count
        raise AssertionError(f"unexpected fetchval SQL: {sql}")

    async def fetch(self, sql, *args):
        if "scheduled_tasks" in sql:
            return [{"cron": c} for c in self.crons]
        if "hours AS" in sql:
            if self.hourly_query_fails:
                raise RuntimeError("hourly activity query failed")
            return [{"sessions_count": c} for c in self.hourly_counts]
        if "model" in sql and "input_tokens" in sql:
            if self.cost_query_fails:
                raise RuntimeError("cost query failed")
            if self.cost_input_tokens == 0 and self.cost_output_tokens == 0:
                return []
            return [
                {
                    "model": self.cost_model,
                    "input_tokens": self.cost_input_tokens,
                    "output_tokens": self.cost_output_tokens,
                    "cached_input_tokens": 0,
                    "cache_creation_tokens": 0,
                }
            ]
        raise AssertionError(f"unexpected fetch SQL: {sql}")


class _FakeSwitchboardPool:
    def __init__(self, *, rows: list[dict] | None = None, fails: bool = False) -> None:
        self.rows = rows or []
        self.fails = fails

    async def fetch(self, sql, *args):
        if self.fails:
            raise RuntimeError("registry unreachable")
        return list(self.rows)


class _FakeDb:
    def __init__(self, *, switchboard: _FakeSwitchboardPool, butlers: dict[str, _FakeButlerPool]):
        self._switchboard = switchboard
        self._butlers = butlers
        self.butler_names = list(butlers)

    def pool(self, name: str):
        if name == "switchboard":
            return self._switchboard
        try:
            return self._butlers[name]
        except KeyError:
            raise KeyError(name) from None

    async def fan_out_with_status(self, sql, *, args=(), butler_names=None):
        """Stand in for the 24h-sessions fan-out used by _fetch_sessions_24h."""
        return {}, []


def _registry_row(
    name: str,
    *,
    last_seen_at: datetime | None = None,
    eligibility_state: str = "active",
    quarantined_at: datetime | None = None,
    quarantine_reason: str | None = None,
    liveness_ttl_seconds: int = 300,
) -> dict:
    return {
        "name": name,
        "last_seen_at": last_seen_at,
        "eligibility_state": eligibility_state,
        "quarantined_at": quarantined_at,
        "quarantine_reason": quarantine_reason,
        "liveness_ttl_seconds": liveness_ttl_seconds,
    }


def _build_app(configs, db, *, online: bool = True):
    app = make_test_app(
        roster_dir=None,  # unused by /board (no _get_roster_dir dependency)
        configs=configs,
        mcp_manager=make_mock_mcp_manager(online=online),
    )
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_pricing] = lambda: PricingConfig(
        models={"claude-x": ModelPricing(input_price_per_token=0.001, output_price_per_token=0.002)}
    )
    return app


async def _get_board(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/butlers/board")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cadence_seconds", "expected"),
    [
        (None, None),
        (60 * 60, "hourly"),
        (24 * 60 * 60, "daily"),
        (7 * 24 * 60 * 60, "weekly"),
        (2 * 60 * 60, "custom"),
        (15 * 60, "custom"),
    ],
)
def test_cadence_label_uses_only_exact_canonical_intervals(
    cadence_seconds: float | None, expected: str | None
) -> None:
    """Named cadence labels must not turn a two-hour or quarter-hour schedule into hourly."""
    assert _cadence_label(cadence_seconds) == expected


async def test_board_happy_path_running_row_has_all_fields():
    now = _now()
    configs = [ButlerConnectionInfo(name="finance", port=41105)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[_registry_row("finance", last_seen_at=now - timedelta(seconds=30))]
        ),
        butlers={
            "finance": _FakeButlerPool(
                crons=["*/15 * * * *"],
                active_count=2,
                last_completed_at=now - timedelta(minutes=5),
                max_concurrent=3,
                hourly_counts=[1] * 24,
                cost_input_tokens=1000,
                cost_output_tokens=500,
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    assert resp.status_code == 200
    payload = resp.json()["data"]
    row = payload["rows"][0]

    assert row["name"] == "finance"
    assert row["activity"] == "running"
    assert row["cell_tone"] == "green"
    assert row["load_pct"] == 67  # round(2/3 * 100)
    assert row["active_session_count"] == 2
    assert row["hourly_total"] == 24
    assert row["cadence_label"] == "custom"
    assert row["cadence_status"] == "on_schedule"
    assert row["cost_today"] and row["cost_today"] > 0
    assert payload["aggregates"]["active"] == 1
    assert payload["aggregates"]["sources_partially_degraded"] is False


async def test_board_cron_expectation_flags_overdue_butler():
    """A daily-cron butler silent for 5 days is 'overdue', not a flat idle."""
    now = _now()
    configs = [ButlerConnectionInfo(name="chronicler", port=41111)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[_registry_row("chronicler", last_seen_at=now - timedelta(days=5))]
        ),
        butlers={
            "chronicler": _FakeButlerPool(
                crons=["0 9 * * *"],  # daily
                active_count=0,
                last_completed_at=now - timedelta(days=5),
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    assert resp.status_code == 200
    row = resp.json()["data"]["rows"][0]

    assert row["cadence_label"] == "daily"
    assert row["cadence_status"] == "overdue"
    assert row["activity"] == "overdue"
    assert row["cell_tone"] == "amber"
    assert resp.json()["data"]["aggregates"]["overdue"] == 1


async def test_board_idle_within_cadence_is_not_overdue():
    """A daily-cron butler silent for 2 hours is on_schedule, not overdue."""
    now = _now()
    configs = [ButlerConnectionInfo(name="chronicler", port=41111)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[_registry_row("chronicler", last_seen_at=now - timedelta(hours=2))]
        ),
        butlers={
            "chronicler": _FakeButlerPool(
                crons=["0 9 * * *"],
                active_count=0,
                last_completed_at=now - timedelta(hours=2),
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    row = resp.json()["data"]["rows"][0]
    assert row["cadence_status"] == "on_schedule"
    assert row["activity"] == "idle"
    assert row["cell_tone"] == "neutral"


async def test_board_quarantined_row_surfaces_reason_and_red_tone():
    now = _now()
    configs = [ButlerConnectionInfo(name="qa", port=41200)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[
                _registry_row(
                    "qa",
                    eligibility_state="quarantined",
                    quarantined_at=now - timedelta(hours=1),
                    quarantine_reason="3 consecutive heartbeat misses",
                )
            ]
        ),
        butlers={"qa": _FakeButlerPool()},
    )
    resp = await _get_board(_build_app(configs, db))
    row = resp.json()["data"]["rows"][0]
    assert row["activity"] == "quarantined"
    assert row["cell_tone"] == "red"
    assert row["quarantine_reason"] == "3 consecutive heartbeat misses"
    assert resp.json()["data"]["aggregates"]["quarantined"] == 1


async def test_board_future_last_seen_at_beyond_tolerance_degrades_to_unknown():
    """A last_seen_at more than 5 min in the future (clock skew) must not
    read as healthy/idle/running -- mirrors the deleted bespoke
    butler_registry CASE's `last_seen_at > NOW() + INTERVAL '5 minutes'`
    guard (bu-y1am9)."""
    now = _now()
    configs = [ButlerConnectionInfo(name="finance", port=41105)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[_registry_row("finance", last_seen_at=now + timedelta(minutes=10))]
        ),
        butlers={
            "finance": _FakeButlerPool(
                crons=["*/15 * * * *"],
                active_count=2,
                max_concurrent=3,
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    assert resp.status_code == 200
    row = resp.json()["data"]["rows"][0]

    assert row["activity"] == "unknown"
    assert row["cell_tone"] == "neutral"
    # The registry heartbeat itself is present (not a source outage) -- only
    # the liveness verdict is downgraded; heartbeat_unavailable stays False
    # and heartbeat_age_seconds is negative, the diagnostic tell for skew.
    assert row["heartbeat_unavailable"] is False
    assert row["heartbeat_age_seconds"] < 0


async def test_board_small_future_last_seen_at_within_tolerance_unaffected():
    """A last_seen_at only slightly ahead (sub-5-min clock drift) is tolerated
    and does not trip the skew guard."""
    now = _now()
    configs = [ButlerConnectionInfo(name="finance", port=41105)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[_registry_row("finance", last_seen_at=now + timedelta(minutes=1))]
        ),
        butlers={
            "finance": _FakeButlerPool(
                crons=["*/15 * * * *"],
                active_count=2,
                max_concurrent=3,
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    assert resp.status_code == 200
    row = resp.json()["data"]["rows"][0]

    assert row["activity"] == "running"
    assert row["cell_tone"] == "green"


async def test_board_registry_failure_degrades_to_unknown_not_fake_health():
    """Registry outage must never render a butler as confidently healthy."""
    configs = [ButlerConnectionInfo(name="finance", port=41105)]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(fails=True),
        butlers={"finance": _FakeButlerPool(active_count=5)},
    )
    resp = await _get_board(_build_app(configs, db))
    payload = resp.json()["data"]
    row = payload["rows"][0]
    assert row["heartbeat_unavailable"] is True
    assert row["activity"] == "unknown"
    assert row["cell_tone"] == "neutral"
    assert row["load_pct"] is None
    # Backend reports 5 active sessions, but with heartbeat unavailable the
    # count is unreliable -- must degrade to 0, never a stale confident 5.
    assert row["active_session_count"] == 0
    assert payload["aggregates"]["registry_source_error"] is True
    assert payload["aggregates"]["sources_partially_degraded"] is True


async def test_board_cost_failure_is_partial_sum_with_error_flag():
    """One butler's cost query failing must not silently zero the fleet total."""
    configs = [
        ButlerConnectionInfo(name="finance", port=41105),
        ButlerConnectionInfo(name="general", port=41101),
    ]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(rows=[_registry_row("finance"), _registry_row("general")]),
        butlers={
            "finance": _FakeButlerPool(cost_input_tokens=1000, cost_output_tokens=500),
            "general": _FakeButlerPool(cost_query_fails=True),
        },
    )
    resp = await _get_board(_build_app(configs, db))
    payload = resp.json()["data"]
    rows_by_name = {r["name"]: r for r in payload["rows"]}
    assert rows_by_name["finance"]["cost_today"] is not None
    assert rows_by_name["general"]["cost_today"] is None
    assert payload["aggregates"]["cost_source_error"] is True
    # Partial sum reflects only the known butler's cost -- never a bare "$0.00".
    assert payload["aggregates"]["total_spend_today"] == rows_by_name["finance"]["cost_today"]


async def test_board_hourly_stripe_failure_flags_error_never_fabricates_zero_stripe():
    """A raising hourly-activity query must flag stripe_source_error, not a bare [0]*24."""
    configs = [
        ButlerConnectionInfo(name="finance", port=41105),
        ButlerConnectionInfo(name="general", port=41101),
    ]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(rows=[_registry_row("finance"), _registry_row("general")]),
        butlers={
            "finance": _FakeButlerPool(hourly_counts=[2] * 24),
            "general": _FakeButlerPool(hourly_query_fails=True),
        },
    )
    resp = await _get_board(_build_app(configs, db))
    payload = resp.json()["data"]
    rows_by_name = {r["name"]: r for r in payload["rows"]}

    assert rows_by_name["finance"]["stripe_source_error"] is False
    assert rows_by_name["general"]["stripe_source_error"] is True
    # The failed butler's stripe/total still degrade to an honest-looking zero
    # array server-side, but the flag is what a client must gate on.
    assert rows_by_name["general"]["hourly_stripe"] == [0] * 24
    assert rows_by_name["general"]["hourly_total"] == 0
    assert payload["aggregates"]["sessions_source_error"] is True
    assert payload["aggregates"]["sources_partially_degraded"] is True
    # Partial sum reflects only the known butler's sessions -- never a bare "0".
    assert payload["aggregates"]["total_sessions_24h"] == rows_by_name["finance"]["hourly_total"]


async def test_board_preserves_stable_roster_order_regardless_of_activity():
    """Rows must never reorder by a live counter (poll-shuffle regression guard)."""
    configs = [
        ButlerConnectionInfo(name="zeta", port=1),
        ButlerConnectionInfo(name="alpha", port=2),
        ButlerConnectionInfo(name="mid", port=3),
    ]
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(rows=[]),
        butlers={
            "zeta": _FakeButlerPool(active_count=0),
            "alpha": _FakeButlerPool(active_count=50),  # most "active" but must stay 2nd
            "mid": _FakeButlerPool(active_count=5),
        },
    )
    resp = await _get_board(_build_app(configs, db))
    names = [r["name"] for r in resp.json()["data"]["rows"]]
    assert names == ["zeta", "alpha", "mid"]


async def test_board_frozen_stale_butler_ignores_stored_eligibility_state():
    """A butler whose last_seen_at is stale (exceeds TTL) must read as 'stale'
    on the board, even if its stored eligibility_state claims 'active' — this
    prevents fabricated calm from a stale stored value. Derives eligibility
    from freshness, not raw stored state (bu-cjk60)."""
    now = _now()
    configs = [ButlerConnectionInfo(name="finance", port=41105)]
    # Set up a butler with:
    # - stored eligibility_state = "active" (the raw stale value)
    # - last_seen_at = 10 minutes ago (exceeds the 300s default TTL)
    # Expected: board row should derive eligibility as "stale", not pass through "active"
    db = _FakeDb(
        switchboard=_FakeSwitchboardPool(
            rows=[
                _registry_row(
                    "finance",
                    last_seen_at=now - timedelta(minutes=10),
                    eligibility_state="active",  # stale stored value, should NOT be used
                    liveness_ttl_seconds=300,  # default TTL
                )
            ]
        ),
        butlers={
            "finance": _FakeButlerPool(
                crons=["*/15 * * * *"],
                active_count=0,
                max_concurrent=3,
            )
        },
    )
    resp = await _get_board(_build_app(configs, db))
    assert resp.status_code == 200
    row = resp.json()["data"]["rows"][0]

    # The board row's eligibility must show "stale" (derived from freshness),
    # not "active" (the raw stored state). This prevents fabricated calm from
    # using a stale stored eligibility_state value.
    assert row["eligibility"] == "stale"
    # Heartbeat is available; the liveness verdict is downgraded by freshness.
    assert row["heartbeat_unavailable"] is False
    assert row["heartbeat_age_seconds"] > 0
