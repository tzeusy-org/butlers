"""Unit tests for read-time staleness derivation.

Covers the Python helper :func:`staleness_band` and the SQL-expression builders
for both stores (identity / narrative):

  - band boundaries: fresh ≤30d, aging ≤180d, stale >180d (per
    ``relationship-entity-lifecycle`` §"Age");
  - COALESCE fallback ordering when ``observed_at`` is NULL, including the
    all-NULL → ``created_at`` case;
  - per-store column mapping (identity uses ``last_seen``; narrative uses
    ``last_confirmed_at`` and has no ``last_seen``);
  - "Same row, different bands over time" (frozen-clock) scenario.

These tests are pure-Python (no DB, no Docker) for the helper; the SQL builders
are asserted structurally.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.tools.relationship.staleness import (
    AGING_MAX_DAYS,
    FRESH_MAX_DAYS,
    StalenessBand,
    identity_staleness_band_sql,
    narrative_staleness_band_sql,
    staleness_band,
)

# A fixed "now" so every age computation is deterministic.
_NOW = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)


def _days_ago(n: float) -> datetime:
    return _NOW - timedelta(days=n)


# ---------------------------------------------------------------------------
# Band boundaries — identity store
# ---------------------------------------------------------------------------


class TestIdentityBandBoundaries:
    @pytest.mark.parametrize(
        ("age_days", "expected"),
        [
            (0, StalenessBand.fresh),
            (1, StalenessBand.fresh),
            (FRESH_MAX_DAYS, StalenessBand.fresh),  # 30d — inclusive upper edge of fresh
            (FRESH_MAX_DAYS + 1, StalenessBand.aging),  # 31d — first aging day
            (90, StalenessBand.aging),
            (AGING_MAX_DAYS, StalenessBand.aging),  # 180d — inclusive upper edge of aging
            (AGING_MAX_DAYS + 1, StalenessBand.stale),  # 181d — first stale day
            (400, StalenessBand.stale),
        ],
    )
    def test_band_for_observed_at(self, age_days, expected):
        band = staleness_band(
            store="identity",
            observed_at=_days_ago(age_days),
            created_at=_days_ago(1000),
            now=_NOW,
        )
        assert band == expected


# ---------------------------------------------------------------------------
# Band boundaries — narrative store
# ---------------------------------------------------------------------------


class TestNarrativeBandBoundaries:
    @pytest.mark.parametrize(
        ("age_days", "expected"),
        [
            (FRESH_MAX_DAYS, StalenessBand.fresh),
            (FRESH_MAX_DAYS + 1, StalenessBand.aging),
            (AGING_MAX_DAYS, StalenessBand.aging),
            (AGING_MAX_DAYS + 1, StalenessBand.stale),
        ],
    )
    def test_band_for_observed_at(self, age_days, expected):
        band = staleness_band(
            store="narrative",
            observed_at=_days_ago(age_days),
            created_at=_days_ago(1000),
            now=_NOW,
        )
        assert band == expected


# ---------------------------------------------------------------------------
# COALESCE fallback ordering
# ---------------------------------------------------------------------------


class TestIdentityFallbackChain:
    """identity: COALESCE(observed_at, last_seen, created_at)."""

    def test_observed_at_wins_over_last_seen(self):
        # observed_at is fresh, last_seen is stale → fresh wins.
        band = staleness_band(
            store="identity",
            observed_at=_days_ago(10),
            last_seen=_days_ago(300),
            created_at=_days_ago(300),
            now=_NOW,
        )
        assert band == StalenessBand.fresh

    def test_falls_back_to_last_seen_when_observed_at_null(self):
        band = staleness_band(
            store="identity",
            observed_at=None,
            last_seen=_days_ago(10),
            created_at=_days_ago(300),
            now=_NOW,
        )
        assert band == StalenessBand.fresh

    def test_all_null_except_created_at(self):
        band = staleness_band(
            store="identity",
            observed_at=None,
            last_seen=None,
            created_at=_days_ago(200),
            now=_NOW,
        )
        assert band == StalenessBand.stale

    def test_last_seen_ignored_for_narrative(self):
        """narrative store has no last_seen — passing it must NOT affect the band."""
        band = staleness_band(
            store="narrative",
            observed_at=None,
            last_seen=_days_ago(10),  # would be fresh if (wrongly) consulted
            last_confirmed_at=None,
            created_at=_days_ago(300),
            now=_NOW,
        )
        assert band == StalenessBand.stale


class TestNarrativeFallbackChain:
    """narrative: COALESCE(observed_at, last_confirmed_at, created_at)."""

    def test_observed_at_wins_over_last_confirmed(self):
        band = staleness_band(
            store="narrative",
            observed_at=_days_ago(10),
            last_confirmed_at=_days_ago(300),
            created_at=_days_ago(300),
            now=_NOW,
        )
        assert band == StalenessBand.fresh

    def test_falls_back_to_last_confirmed_when_observed_at_null(self):
        band = staleness_band(
            store="narrative",
            observed_at=None,
            last_confirmed_at=_days_ago(100),
            created_at=_days_ago(400),
            now=_NOW,
        )
        assert band == StalenessBand.aging

    def test_all_null_except_created_at(self):
        band = staleness_band(
            store="narrative",
            observed_at=None,
            last_confirmed_at=None,
            created_at=_days_ago(15),
            now=_NOW,
        )
        assert band == StalenessBand.fresh


# ---------------------------------------------------------------------------
# Same row, different bands over time (frozen-clock scenario from the spec)
# ---------------------------------------------------------------------------


class TestSameRowDifferentBandsOverTime:
    def test_fresh_then_stale_with_no_writes(self):
        observed = datetime(2026, 1, 1, tzinfo=UTC)
        created = observed

        # Read 20 days after observation → fresh.
        band_early = staleness_band(
            store="identity",
            observed_at=observed,
            created_at=created,
            now=observed + timedelta(days=20),
        )
        assert band_early == StalenessBand.fresh

        # Read 200 days after observation, no new writes → stale.
        band_late = staleness_band(
            store="identity",
            observed_at=observed,
            created_at=created,
            now=observed + timedelta(days=200),
        )
        assert band_late == StalenessBand.stale


# ---------------------------------------------------------------------------
# Invalid store guard
# ---------------------------------------------------------------------------


def test_unknown_store_raises():
    with pytest.raises(ValueError, match="Unknown store"):
        staleness_band(
            store="bogus",  # type: ignore[arg-type]
            observed_at=None,
            created_at=_NOW,
            now=_NOW,
        )


# ---------------------------------------------------------------------------
# SQL-expression builders — structural assertions
# ---------------------------------------------------------------------------


class TestSqlBuilders:
    def test_identity_sql_uses_correct_fallback_chain(self):
        sql = identity_staleness_band_sql("f")
        assert "COALESCE(f.observed_at, f.last_seen, f.created_at)" in sql
        # Band thresholds must match the Python helper.
        assert f"INTERVAL '{FRESH_MAX_DAYS} days'" in sql
        assert f"INTERVAL '{AGING_MAX_DAYS} days'" in sql
        assert "'fresh'" in sql and "'aging'" in sql and "'stale'" in sql

    def test_narrative_sql_uses_correct_fallback_chain(self):
        sql = narrative_staleness_band_sql("m")
        assert "COALESCE(m.observed_at, m.last_confirmed_at, m.created_at)" in sql
        # narrative store must NOT reference last_seen.
        assert "last_seen" not in sql

    def test_identity_sql_respects_alias(self):
        sql = identity_staleness_band_sql("alias_x")
        assert "alias_x.observed_at" in sql
