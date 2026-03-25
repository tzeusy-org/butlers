"""Tests for daily briefing contribution jobs (bu-bxea).

Covers:
- Tasks 2.2-2.3: contribution_key, validate_contribution, today_sgt helpers
- Tasks 3.1-3.7: each specialist butler contribution job (mocked DB, envelope structure)
- Task 4.3 (partial): registry key presence for daily_briefing_contribution in daemon registry
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.briefing import (
    contribution_key,
    run_education_briefing_contribution,
    run_finance_briefing_contribution,
    run_health_briefing_contribution,
    run_home_briefing_contribution,
    run_relationship_briefing_contribution,
    run_travel_briefing_contribution,
    today_sgt,
    validate_contribution,
)

pytestmark = pytest.mark.unit

_SGT = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_valid_envelope(butler: str = "health", date_str: str = "2026-03-25") -> dict[str, Any]:
    return {
        "butler": butler,
        "date": date_str,
        "has_updates": True,
        "highlights": [
            {"category": "test", "text": "some text", "priority": "medium"},
        ],
        "summary": "Test summary.",
    }


def _make_pool(*, fetchval_return: Any = None, fetch_return: list | None = None) -> MagicMock:
    """Return a minimal mock asyncpg pool."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


# ---------------------------------------------------------------------------
# contribution_key and today_sgt helpers (Task 2.2)
# ---------------------------------------------------------------------------


class TestContributionKey:
    def test_format(self):
        assert contribution_key("2026-03-25") == "briefing/daily/2026-03-25"

    def test_different_dates(self):
        assert contribution_key("2026-01-01") == "briefing/daily/2026-01-01"
        assert contribution_key("2099-12-31") == "briefing/daily/2099-12-31"


class TestTodaySgt:
    def test_returns_date(self):
        result = today_sgt()
        assert isinstance(result, date)

    def test_sgt_offset(self):
        # today_sgt() should be in UTC+8; we can't assert the exact date without
        # mocking time, but we can verify it stays within ±1 day of UTC today
        sgt_date = today_sgt()
        utc_date = datetime.now(tz=UTC).date()
        delta = abs((sgt_date - utc_date).days)
        assert delta <= 1, f"SGT date {sgt_date} is more than 1 day from UTC date {utc_date}"


# ---------------------------------------------------------------------------
# validate_contribution (Task 2.3)
# ---------------------------------------------------------------------------


class TestValidateContribution:
    def test_valid_envelope(self):
        env = _make_valid_envelope()
        result = validate_contribution(env)
        assert result is env

    def test_missing_butler_raises(self):
        env = _make_valid_envelope()
        del env["butler"]
        with pytest.raises(ValueError, match="'butler'"):
            validate_contribution(env)

    def test_missing_date_raises(self):
        env = _make_valid_envelope()
        del env["date"]
        with pytest.raises(ValueError, match="'date'"):
            validate_contribution(env)

    def test_missing_summary_raises(self):
        env = _make_valid_envelope()
        del env["summary"]
        with pytest.raises(ValueError, match="'summary'"):
            validate_contribution(env)

    def test_missing_has_updates_raises(self):
        env = _make_valid_envelope()
        del env["has_updates"]
        with pytest.raises(ValueError, match="'has_updates'"):
            validate_contribution(env)

    def test_has_updates_must_be_bool(self):
        env = _make_valid_envelope()
        env["has_updates"] = "yes"
        with pytest.raises(ValueError, match="bool"):
            validate_contribution(env)

    def test_missing_highlights_raises(self):
        env = _make_valid_envelope()
        del env["highlights"]
        with pytest.raises(ValueError, match="'highlights'"):
            validate_contribution(env)

    def test_highlights_must_be_list(self):
        env = _make_valid_envelope()
        env["highlights"] = "not a list"
        with pytest.raises(ValueError, match="list"):
            validate_contribution(env)

    def test_highlight_missing_category(self):
        env = _make_valid_envelope()
        env["highlights"] = [{"text": "foo", "priority": "low"}]
        with pytest.raises(ValueError, match="'category'"):
            validate_contribution(env)

    def test_highlight_missing_text(self):
        env = _make_valid_envelope()
        env["highlights"] = [{"category": "foo", "priority": "low"}]
        with pytest.raises(ValueError, match="'text'"):
            validate_contribution(env)

    def test_highlight_missing_priority(self):
        env = _make_valid_envelope()
        env["highlights"] = [{"category": "foo", "text": "bar"}]
        with pytest.raises(ValueError, match="'priority'"):
            validate_contribution(env)

    def test_highlight_field_must_be_str(self):
        env = _make_valid_envelope()
        env["highlights"] = [{"category": 123, "text": "bar", "priority": "low"}]
        with pytest.raises(ValueError, match="str"):
            validate_contribution(env)

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="dict"):
            validate_contribution("not a dict")

    def test_empty_highlights_valid(self):
        env = _make_valid_envelope()
        env["highlights"] = []
        env["has_updates"] = False
        result = validate_contribution(env)
        assert result["highlights"] == []

    def test_multiple_highlights_valid(self):
        env = _make_valid_envelope()
        env["highlights"] = [
            {"category": "a", "text": "first", "priority": "high"},
            {"category": "b", "text": "second", "priority": "low"},
        ]
        result = validate_contribution(env)
        assert len(result["highlights"]) == 2


# ---------------------------------------------------------------------------
# Health butler contribution job (Task 3.1)
# ---------------------------------------------------------------------------


class TestHealthBriefingContribution:
    async def test_no_active_medications_no_updates(self):
        """With no active medications and no weight measurements, no updates expected."""
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # missed_rows
                [{"cnt": 0}],  # taken_rows
                [],  # weight_row (fetch returns list, but fetchrow is used for weight)
            ]
        )
        pool.fetchrow = AsyncMock(return_value=None)

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_health_briefing_contribution(pool, None)

        assert result["butler"] == "health"
        assert result["has_updates"] is False
        assert result["missed_doses"] == 0
        mock_set.assert_awaited_once()
        envelope = mock_set.call_args[0][2]
        assert envelope["butler"] == "health"
        assert isinstance(envelope["date"], str)
        assert envelope["has_updates"] is False
        assert envelope["highlights"] == []

    async def test_missed_doses_creates_highlight(self):
        """Missed doses today should create a high-priority highlight."""
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [{"name": "Vitamin D", "frequency": "daily", "schedule": []}],  # missed_rows
                [{"cnt": 1}],  # taken_rows
                [],  # (not used — fetchrow handles weight)
            ]
        )
        pool.fetchrow = AsyncMock(return_value=None)  # no weight measurement

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_health_briefing_contribution(pool, None)

        assert result["has_updates"] is True
        assert result["missed_doses"] == 1
        envelope = mock_set.call_args[0][2]
        assert envelope["has_updates"] is True
        assert any(h["category"] == "medication" for h in envelope["highlights"])
        # Validate the envelope structure
        validate_contribution(envelope)

    async def test_weight_measurement_adds_highlight(self):
        """Recent weight measurement should produce a weight highlight."""
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # no missed doses
                [{"cnt": 2}],  # taken doses
            ]
        )
        pool.fetchrow = AsyncMock(
            return_value={"value": {"value": 70.5, "unit": "kg"}, "measured_at": datetime.now(UTC)}
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_health_briefing_contribution(pool, None)

        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "weight" for h in envelope["highlights"])
        assert result["has_updates"] is True

    async def test_returns_correct_butler_name(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[[], [{"cnt": 0}]])
        pool.fetchrow = AsyncMock(return_value=None)
        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock),
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_health_briefing_contribution(pool, None)
        assert result["butler"] == "health"


# ---------------------------------------------------------------------------
# Finance butler contribution job (Task 3.2)
# ---------------------------------------------------------------------------


class TestFinanceBriefingContribution:
    async def test_no_data_no_updates(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # bills_rows
                [],  # anomaly_rows
                [],  # sub_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_finance_briefing_contribution(pool, None)

        assert result["butler"] == "finance"
        assert result["has_updates"] is False
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert envelope["has_updates"] is False

    async def test_bill_due_48h_creates_highlight(self):
        today = today_sgt()
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "payee": "Electricity",
                        "amount": 85.50,
                        "currency": "USD",
                        "due_date": today,
                        "status": "pending",
                    }
                ],  # bills_rows
                [],  # anomaly_rows
                [],  # sub_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_finance_briefing_contribution(pool, None)

        assert result["has_updates"] is True
        assert result["bills_due_48h"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "bills" for h in envelope["highlights"])

    async def test_spending_anomaly_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # bills_rows
                [
                    {
                        "category": "dining",
                        "daily_recent": 50.0,
                        "daily_avg": 20.0,
                        "ratio": 2.5,
                    }
                ],  # anomaly_rows
                [],  # sub_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_finance_briefing_contribution(pool, None)

        assert result["spending_anomalies"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "spending" for h in envelope["highlights"])

    async def test_subscription_renewal_creates_highlight(self):
        today = today_sgt()
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # bills_rows
                [],  # anomaly_rows
                [
                    {
                        "service": "Netflix",
                        "amount": 15.99,
                        "currency": "USD",
                        "next_renewal": today + timedelta(days=3),
                        "auto_renew": True,
                    }
                ],  # sub_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_finance_briefing_contribution(pool, None)

        assert result["subscription_renewals"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "subscriptions" for h in envelope["highlights"])


# ---------------------------------------------------------------------------
# Relationship butler contribution job (Task 3.3)
# ---------------------------------------------------------------------------


class TestRelationshipBriefingContribution:
    async def test_no_data_no_updates(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # birthday_rows
                [],  # reminder_rows
                [],  # gap_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_relationship_briefing_contribution(pool, None)

        assert result["butler"] == "relationship"
        assert result["has_updates"] is False
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)

    async def test_birthday_upcoming_creates_highlight(self):
        today = today_sgt()
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "name": "Alice",
                        "label": "Birthday",
                        "month": today.month,
                        "day": today.day,
                        "year": 1990,
                    }
                ],  # birthday_rows
                [],  # reminder_rows
                [],  # gap_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_relationship_briefing_contribution(pool, None)

        assert result["birthdays_upcoming"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "birthdays" for h in envelope["highlights"])

    async def test_overdue_reminder_creates_high_priority_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # birthday_rows
                [
                    {
                        "name": "Bob",
                        "label": "Call back",
                        "next_trigger_at": datetime.now(UTC) - timedelta(hours=2),
                        "is_overdue": True,
                    }
                ],  # reminder_rows
                [],  # gap_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_relationship_briefing_contribution(pool, None)

        assert result["follow_ups_overdue"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        overdue_hl = [h for h in envelope["highlights"] if h["category"] == "follow-ups"]
        assert len(overdue_hl) == 1
        assert overdue_hl[0]["priority"] == "high"

    async def test_interaction_gap_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # birthday_rows
                [],  # reminder_rows
                [
                    {
                        "name": "Charlie",
                        "stay_in_touch_days": 30,
                        "days_since_last": 45,
                    }
                ],  # gap_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_relationship_briefing_contribution(pool, None)

        assert result["interaction_gaps"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "interaction-gaps" for h in envelope["highlights"])


# ---------------------------------------------------------------------------
# Travel butler contribution job (Task 3.4)
# ---------------------------------------------------------------------------


class TestTravelBriefingContribution:
    async def test_no_trips_no_updates(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # dep_rows
                [],  # checkin_rows
                # doc query only fires if dep_rows non-empty
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_travel_briefing_contribution(pool, None)

        assert result["butler"] == "travel"
        assert result["has_updates"] is False
        assert result["departures_48h"] == 0
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)

    async def test_departure_within_48h_creates_highlight(self):
        import uuid

        trip_id = str(uuid.uuid4())
        now_utc = datetime.now(UTC)
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "type": "flight",
                        "carrier": "SQ",
                        "departure_at": now_utc + timedelta(hours=6),
                        "departure_city": "SIN",
                        "arrival_city": "NRT",
                        "confirmation_number": "ABC123",
                        "pnr": "K9X4TZ",
                        "seat": "12A",
                        "trip_name": "Tokyo Trip",
                        "trip_id": trip_id,
                    }
                ],  # dep_rows
                [],  # checkin_rows
                [{"id": trip_id, "name": "Tokyo Trip", "doc_count": 0}],  # missing docs
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_travel_briefing_contribution(pool, None)

        assert result["departures_48h"] == 1
        assert result["has_updates"] is True
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "departures" for h in envelope["highlights"])

    async def test_checkin_today_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # dep_rows (no departures so no doc query)
                [
                    {
                        "name": "Shinjuku Hotel",
                        "check_in": datetime.now(UTC) + timedelta(hours=3),
                        "type": "hotel",
                        "trip_name": "Tokyo Trip",
                    }
                ],  # checkin_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_travel_briefing_contribution(pool, None)

        assert result["checkins_today"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "check-ins" for h in envelope["highlights"])


# ---------------------------------------------------------------------------
# Education butler contribution job (Task 3.5)
# ---------------------------------------------------------------------------


class TestEducationBriefingContribution:
    async def test_no_data_no_updates(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # pending_rows
                [],  # streak_risk_rows
            ]
        )
        pool.fetchrow = AsyncMock(return_value=None)  # no current topic

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_education_briefing_contribution(pool, None)

        assert result["butler"] == "education"
        assert result["pending_reviews"] == 0
        assert result["streaks_at_risk"] == 0
        assert result["current_topic"] is None
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)

    async def test_pending_reviews_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "id": "node-1",
                        "label": "Recursion",
                        "map_title": "Python",
                        "next_review_at": datetime.now(UTC) - timedelta(hours=1),
                    },
                    {
                        "id": "node-2",
                        "label": "Closures",
                        "map_title": "Python",
                        "next_review_at": datetime.now(UTC) - timedelta(hours=2),
                    },
                ],  # pending_rows
                [],  # streak_risk_rows
            ]
        )
        pool.fetchrow = AsyncMock(return_value={"title": "Python", "mastered": 5, "total": 20})

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_education_briefing_contribution(pool, None)

        assert result["pending_reviews"] == 2
        assert result["has_updates"] is True
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "reviews" for h in envelope["highlights"])

    async def test_streak_at_risk_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # pending_rows
                [
                    {
                        "map_id": "map-1",
                        "title": "Python",
                        "days_active_last_4": 4,
                    }
                ],  # streak_risk_rows
            ]
        )
        pool.fetchrow = AsyncMock(return_value={"title": "Python", "mastered": 10, "total": 25})

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_education_briefing_contribution(pool, None)

        assert result["streaks_at_risk"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert any(h["category"] == "streak" for h in envelope["highlights"])

    async def test_current_topic_adds_low_priority_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[[], []])
        pool.fetchrow = AsyncMock(return_value={"title": "Calculus", "mastered": 3, "total": 10})

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_education_briefing_contribution(pool, None)

        assert result["current_topic"] == "Calculus"
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        topic_hl = [h for h in envelope["highlights"] if h["category"] == "current-topic"]
        assert len(topic_hl) == 1
        assert topic_hl[0]["priority"] == "low"


# ---------------------------------------------------------------------------
# Home butler contribution job (Task 3.6)
# ---------------------------------------------------------------------------


class TestHomeBriefingContribution:
    async def test_all_nominal_no_updates(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # device_alert_rows (unavailable)
                [],  # temp_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_home_briefing_contribution(pool, None)

        assert result["butler"] == "home"
        assert result["has_updates"] is False
        assert result["device_alerts"] == 0
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        assert envelope["summary"] == "Home systems nominal."

    async def test_unavailable_device_creates_high_priority_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [
                    {
                        "entity_id": "sensor.basement_sensor",
                        "state": "unavailable",
                        "attributes": {"friendly_name": "Basement Sensor"},
                    }
                ],  # device_alert_rows
                [],  # temp_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_home_briefing_contribution(pool, None)

        assert result["device_alerts"] == 1
        assert result["has_updates"] is True
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        device_hl = [h for h in envelope["highlights"] if h["category"] == "device-alerts"]
        assert len(device_hl) == 1
        assert device_hl[0]["priority"] == "high"

    async def test_temperature_outlier_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                [],  # device_alert_rows
                [
                    {
                        "entity_id": "sensor.outdoor_temperature",
                        "state": "8.5",
                        "attributes": {
                            "friendly_name": "Outdoor Temperature",
                            "unit_of_measurement": "°C",
                        },
                    }
                ],  # temp_rows
            ]
        )

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch("butlers.jobs.briefing.state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_home_briefing_contribution(pool, None)

        assert result["temp_outliers"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        env_hl = [h for h in envelope["highlights"] if h["category"] == "environment"]
        assert len(env_hl) == 1

    async def test_energy_anomaly_from_state_creates_highlight(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[[], []])

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock) as mock_set,
            patch(
                "butlers.jobs.briefing.state_list",
                new_callable=AsyncMock,
                return_value=["home/energy/anomaly/2026-03-25"],
            ),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock),
        ):
            result = await run_home_briefing_contribution(pool, None)

        assert result["energy_anomalies"] == 1
        envelope = mock_set.call_args[0][2]
        validate_contribution(envelope)
        energy_hl = [h for h in envelope["highlights"] if h["category"] == "energy"]
        assert len(energy_hl) == 1


# ---------------------------------------------------------------------------
# Cleanup: old keys are deleted (Task 2.2)
# ---------------------------------------------------------------------------


class TestContributionCleanup:
    async def test_old_keys_are_deleted(self):
        """Keys older than 7 days should be deleted during _write_contribution."""
        old_date = (today_sgt() - timedelta(days=8)).isoformat()
        recent_date = today_sgt().isoformat()
        old_key = f"briefing/daily/{old_date}"
        recent_key = f"briefing/daily/{recent_date}"

        pool = _make_pool()
        pool.fetch = AsyncMock(side_effect=[[], [{"cnt": 0}]])
        pool.fetchrow = AsyncMock(return_value=None)

        with (
            patch("butlers.jobs.briefing.state_set", new_callable=AsyncMock),
            patch(
                "butlers.jobs.briefing.state_list",
                new_callable=AsyncMock,
                return_value=[old_key, recent_key],
            ),
            patch("butlers.jobs.briefing.state_delete", new_callable=AsyncMock) as mock_delete,
        ):
            await run_health_briefing_contribution(pool, None)

        deleted_keys = [call.args[1] for call in mock_delete.call_args_list]
        assert old_key in deleted_keys, f"Old key {old_key!r} should have been deleted"
        assert recent_key not in deleted_keys, (
            f"Recent key {recent_key!r} should NOT have been deleted"
        )


# ---------------------------------------------------------------------------
# Registry registration (Task 4.3 partial)
# ---------------------------------------------------------------------------


class TestDaemonRegistry:
    def test_all_specialist_butlers_have_briefing_contribution_job(self):
        """All 6 specialist butlers must have daily_briefing_contribution registered."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        specialist_butlers = ("health", "finance", "relationship", "travel", "education", "home")
        for name in specialist_butlers:
            jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(name, {})
            assert "daily_briefing_contribution" in jobs, (
                f"Butler {name!r} missing 'daily_briefing_contribution' in registry"
            )

    def test_general_butler_does_not_have_briefing_contribution_job(self):
        """General butler runs collect_briefing_contributions, not daily_briefing_contribution."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        general_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("general", {})
        # general should NOT have daily_briefing_contribution (it aggregates instead)
        assert "daily_briefing_contribution" not in general_jobs, (
            "General butler should not have 'daily_briefing_contribution' — "
            "it runs 'collect_briefing_contributions'"
        )

    def test_job_handlers_are_callable(self):
        """All registered job handlers must be callables."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        specialist_butlers = ("health", "finance", "relationship", "travel", "education", "home")
        for name in specialist_butlers:
            handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY[name]["daily_briefing_contribution"]
            assert callable(handler), (
                f"Handler for {name!r}/daily_briefing_contribution is not callable"
            )
