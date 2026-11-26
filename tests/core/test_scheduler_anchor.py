"""The scheduler's one anchor predicate and the creator's declared-anchor rules (bu-5ii4gj.5 P6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.scheduler.anchor import Anchor, effective_anchor, resolve_declared_anchor

pytestmark = pytest.mark.unit

NEXT = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def row(**kw):
    base = {"cron": "0 9 * * *", "timezone": "UTC", "task_type": "cron", "next_run_at": NEXT}
    base.update(kw)
    return base


@pytest.mark.parametrize("tz", ["UTC", "utc", "", None, "  "])
def test_the_sentinel_is_owner_local(tz):
    assert effective_anchor(row(timezone=tz)) == Anchor("owner_local")


def test_a_literal_zone_is_pinned_to_it_and_etc_utc_is_a_real_pin():
    assert effective_anchor(row(timezone="Europe/London")) == Anchor("zone_pinned", "Europe/London")
    assert effective_anchor(row(timezone="Etc/UTC")) == Anchor("zone_pinned", "Etc/UTC")


def test_deadline_rows_and_one_shot_windows_are_absolute():
    assert effective_anchor(row(task_type="deadline")).kind == "absolute"
    assert effective_anchor(row(cron=None)).kind == "absolute"
    reminder = row(until_at=NEXT + timedelta(seconds=30))
    assert effective_anchor(reminder).kind == "absolute"
    series = row(until_at=NEXT + timedelta(days=30))
    assert effective_anchor(series).kind == "owner_local"  # a bounded series is still wall-clock


def test_a_declared_kind_wins_and_an_unknown_one_is_ignored():
    declared = row(timezone="Europe/London", anchor_kind="zone_pinned", anchor_zone="Europe/London")
    assert effective_anchor(declared) == Anchor("zone_pinned", "Europe/London")
    assert effective_anchor(row(anchor_kind="interval")) == Anchor("interval")
    assert effective_anchor(row(anchor_kind="bogus")) == Anchor("owner_local")  # derived


class TestDeclaredAnchor:
    def test_nothing_declared_changes_nothing(self):
        assert resolve_declared_anchor(None, None, "Europe/London") == (None, None, "Europe/London")
        with pytest.raises(ValueError, match="needs an anchor_kind"):
            resolve_declared_anchor(None, "Europe/London", None)

    def test_owner_local_keeps_the_sentinel_and_refuses_a_zone(self):
        assert resolve_declared_anchor("owner_local", None, None) == ("owner_local", None, None)
        with pytest.raises(ValueError, match="cannot also name a timezone"):
            resolve_declared_anchor("owner_local", None, "Europe/London")
        with pytest.raises(ValueError, match="only for zone_pinned"):
            resolve_declared_anchor("owner_local", "Europe/London", None)

    def test_zone_pinned_stores_the_zone_as_the_timezone(self):
        assert resolve_declared_anchor("zone_pinned", "Europe/London", None) == (
            "zone_pinned",
            "Europe/London",
            "Europe/London",
        )
        with pytest.raises(ValueError, match="must equal"):
            resolve_declared_anchor("zone_pinned", "Europe/London", "Asia/Tokyo")

    @pytest.mark.parametrize("zone", [None, "", "UTC", "Not/AZone"])
    def test_zone_pinned_refuses_a_missing_sentinel_or_unknown_zone(self, zone):
        with pytest.raises(ValueError):
            resolve_declared_anchor("zone_pinned", zone, None)

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError, match="anchor_kind must be one of"):
            resolve_declared_anchor("bogus", None, None)


def test_a_declared_recurring_anchor_on_a_non_cron_row_is_derived_instead():
    for declared in ("owner_local", "zone_pinned"):
        row = {
            "anchor_kind": declared,
            "anchor_zone": "Europe/London",
            "cron": "0 0 * * *",
            "task_type": "deadline",
            "timezone": "UTC",
        }
        assert effective_anchor(row).kind == "absolute", declared
