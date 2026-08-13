"""Regression coverage for the day-close cache identity contract."""

from datetime import date

import pytest

from butlers.chronicler.day_close_cache import (
    InvalidDayCloseTimezoneError,
    MissingDayCloseTimezoneError,
    day_close_cache_key,
    resolve_day_close_timezone,
)

pytestmark = pytest.mark.unit


def test_day_close_cache_key_binds_exact_date_and_timezone() -> None:
    """Same ISO date in distinct local timezones must never share a cache row."""
    target = date(2026, 3, 8)

    singapore = day_close_cache_key(target, "Asia/Singapore")
    los_angeles = day_close_cache_key(target, "America/Los_Angeles")

    assert singapore == "day_close:2026-03-08:tz:Asia/Singapore"
    assert los_angeles == "day_close:2026-03-08:tz:America/Los_Angeles"
    assert singapore != los_angeles


def test_resolve_day_close_timezone_preserves_the_exact_validated_input() -> None:
    """Validation proves an IANA name without canonicalizing its cache identity."""
    timezone_name, timezone = resolve_day_close_timezone("America/Los_Angeles")

    assert timezone_name == "America/Los_Angeles"
    assert str(timezone) == "America/Los_Angeles"


def test_resolve_day_close_timezone_keeps_valid_aliases_as_distinct_identities() -> None:
    """Resolvable aliases are accepted but never canonicalized into one tuple."""
    target = date(2026, 3, 8)
    alias_name, _ = resolve_day_close_timezone("US/Pacific")
    canonical_name, _ = resolve_day_close_timezone("America/Los_Angeles")

    assert alias_name == "US/Pacific"
    assert canonical_name == "America/Los_Angeles"
    assert day_close_cache_key(target, alias_name) != day_close_cache_key(target, canonical_name)


@pytest.mark.parametrize("timezone", ["", "Not/A/Timezone"])
def test_resolve_day_close_timezone_rejects_invalid_values(timezone: str) -> None:
    with pytest.raises(InvalidDayCloseTimezoneError):
        resolve_day_close_timezone(timezone)


def test_resolve_day_close_timezone_rejects_an_omitted_value() -> None:
    with pytest.raises(MissingDayCloseTimezoneError):
        resolve_day_close_timezone(None)
